"""Terminal onboarding CLI for SellerClaw Agent (talks to local Agent Server only)."""

from __future__ import annotations

import json
import os
import secrets as _secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.theme import Theme

try:
    import questionary
    from questionary import Style as _QStyle
except ImportError:  # pragma: no cover - optional dependency fallback
    questionary = None  # type: ignore[assignment]
    _QStyle = None  # type: ignore[assignment]

_THEME = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        "hint": "dim",
        "label": "bold",
    },
)

__all__ = ["agent_base_url", "agent_root", "main", "parse_command"]

_cli_local_api_key_cache: str | None = None
_VALID_AGENT_ENVS = ("local", "staging", "production")


def _clear_local_control_plane_key_cache() -> None:
    """Reset cached local API key (tests)."""
    global _cli_local_api_key_cache
    _cli_local_api_key_cache = None


def _require_agent_env() -> str:
    """Return ``AGENT_ENV`` value, or raise with a clear message (no fallback)."""
    profile = (os.environ.get("AGENT_ENV") or "").strip()
    if not profile:
        msg = (
            "AGENT_ENV is not set. Run ./setup.sh (default env: production) "
            "or export AGENT_ENV=local|staging|production."
        )
        raise RuntimeError(msg)
    if profile not in _VALID_AGENT_ENVS:
        msg = (
            f"Invalid AGENT_ENV='{profile}'. "
            f"Expected one of: {', '.join(_VALID_AGENT_ENVS)}."
        )
        raise RuntimeError(msg)
    return profile


def _compose_profile_env_file(agent_dir: Path) -> Path:
    """Resolve ``.env.<profile>``. Errors out when ``AGENT_ENV`` is unset or file is missing."""
    profile = _require_agent_env()
    path = agent_dir / f".env.{profile}"
    if not path.is_file():
        msg = f"Environment file not found: {path}"
        raise RuntimeError(msg)
    return path


def _compose_env_file_args(agent_dir: Path) -> list[str]:
    return ["--env-file", str(_compose_profile_env_file(agent_dir))]


def _local_api_key_path(agent_dir: Path) -> Path:
    """Host-side path to the local control-plane API key (same volume as container)."""
    return agent_dir / "data" / "local_api_key"


def _ensure_local_api_key(agent_dir: Path) -> str:
    """Read (or create once) the local API key file shared with the container.

    Precedence: env override ``SELLERCLAW_LOCAL_API_KEY`` > file in ``./data`` > new random token.
    The file is owned by the host user so later reads never require Docker.
    """
    env_key = (os.environ.get("SELLERCLAW_LOCAL_API_KEY") or "").strip()
    if env_key:
        return env_key
    path = _local_api_key_path(agent_dir)
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return raw
    path.parent.mkdir(parents=True, exist_ok=True)
    token = _secrets.token_urlsafe(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def _local_control_plane_auth_headers(base_url: str) -> dict[str, str]:
    """Bearer for control-plane routes, read from the on-disk key shared with the container.

    ``base_url`` is kept for backwards compatibility but is no longer used for a
    network bootstrap call — the CLI and the container read the exact same file.
    """
    del base_url  # unused: the file-based flow avoids an HTTP round-trip.
    global _cli_local_api_key_cache
    if _cli_local_api_key_cache is None:
        _cli_local_api_key_cache = _ensure_local_api_key(agent_root())
    return {"Authorization": f"Bearer {_cli_local_api_key_cache}"}


def _console() -> Console:
    return Console(theme=_THEME)


def agent_root() -> Path:
    """Directory containing ``docker-compose.yml`` (sellerclaw-agent package parent)."""
    return Path(__file__).resolve().parent.parent


def agent_base_url() -> str:
    """Base URL of the Agent Server (mapped host port in docker-compose)."""
    return "http://127.0.0.1:8001"


_QUESTIONARY_STYLE = (
    _QStyle(
        [
            ("qmark", "fg:#00afff bold"),
            ("question", "bold"),
            ("pointer", "fg:#00afff bold"),
            ("highlighted", "fg:#00afff bold"),
            ("selected", "fg:#00ff87 bold"),
            ("answer", "fg:#00afff bold"),
            ("instruction", "fg:#808080 italic"),
        ],
    )
    if _QStyle is not None
    else None
)


def _select_option(
    console: Console,
    title: str,
    options: list[tuple[str, str]],
    *,
    default_value: str | None = None,
) -> str:
    """Arrow-key select menu. Falls back to numbered Rich prompt without TTY."""
    if questionary is not None and sys.stdin.isatty():
        choices = [
            questionary.Choice(title=label, value=value) for value, label in options
        ]
        default_choice = None
        if default_value is not None:
            for c in choices:
                if c.value == default_value:
                    default_choice = c
                    break
        answer = questionary.select(
            title,
            choices=choices,
            default=default_choice,
            style=_QUESTIONARY_STYLE,
            instruction="(use ↑/↓ and Enter)",
            qmark="›",
        ).ask()
        if answer is None:
            raise SystemExit(1)
        return str(answer)

    console.print(f"\n[label]{title}[/label]\n")
    for idx, (_value, label) in enumerate(options, start=1):
        console.print(f"  [info]{idx}[/info]  {label}")
    console.print()
    choices = [str(i) for i in range(1, len(options) + 1)]
    default_idx = "1"
    if default_value is not None:
        for i, (value, _label) in enumerate(options, start=1):
            if value == default_value:
                default_idx = str(i)
                break
    picked = Prompt.ask("Your choice", choices=choices, default=default_idx)
    return options[int(picked) - 1][0]


def _confirm(console: Console, question: str, *, default: bool = True) -> bool:
    """Yes/no prompt with arrow-friendly UI when possible."""
    if questionary is not None and sys.stdin.isatty():
        answer = questionary.confirm(
            question,
            default=default,
            style=_QUESTIONARY_STYLE,
            qmark="›",
            auto_enter=False,
        ).ask()
        if answer is None:
            raise SystemExit(1)
        return bool(answer)
    return Confirm.ask(question, default=default, console=console)


def _active_env_label() -> str:
    """Human-readable label for the current ``AGENT_ENV`` (never guesses a default)."""
    return (os.environ.get("AGENT_ENV") or "").strip() or "<unset>"


def parse_command(argv: list[str]) -> str:
    """First CLI argument as command name; default ``setup``."""
    if not argv:
        return "setup"
    if argv[0] in ("-h", "--help", "help"):
        return "help"
    return argv[0]


# ---------------------------------------------------------------------------
# Docker Compose helpers
# ---------------------------------------------------------------------------

def _docker_compose_prefix() -> list[str] | None:
    if shutil.which("docker") is None:
        return None
    version_check = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if version_check.returncode == 0:
        return ["docker", "compose"]
    return None


def _diagnose_compose_failure(output: str) -> tuple[str, list[str]]:
    """Map raw compose stderr/stdout to a short problem title and a list of hints."""
    lo = output.lower()
    if (
        "failed to do request" in lo
        or "dial tcp" in lo
        or "i/o timeout" in lo
        or "tls handshake timeout" in lo
        or "connection refused" in lo
        or "network is unreachable" in lo
    ) and ("failed to solve" in lo or "failed to resolve source" in lo or "pull access denied" in lo or "manifests" in lo):
        return (
            "Could not download the Docker base image.",
            [
                "Check that this machine has Internet access (try: curl -I https://ghcr.io).",
                "If you're on a corporate network or VPN, allow outbound HTTPS to ghcr.io "
                "and docker.io, or configure a proxy for Docker "
                "(https://docs.docker.com/network/proxy/).",
                "If the image exists locally already, pull it once manually: "
                "docker pull ghcr.io/openclaw/openclaw:2026.4.15",
            ],
        )
    if "no such host" in lo or ("lookup" in lo and "dial tcp" in lo):
        return (
            "DNS lookup failed while pulling a Docker image.",
            [
                "Check /etc/resolv.conf and that your DNS server is reachable.",
                "If you're behind a VPN/proxy, make sure DNS is routed correctly.",
                "Retry: ./setup.sh",
            ],
        )
    if "toomanyrequests" in lo or "rate limit" in lo:
        return (
            "Docker registry rate limit reached.",
            [
                "Wait a few minutes and try again.",
                "Authenticate with Docker Hub / GHCR to raise the limit: docker login ghcr.io",
            ],
        )
    if "unauthorized" in lo or "pull access denied" in lo or "requested access to the resource is denied" in lo:
        return (
            "Access to the Docker image was denied.",
            [
                "Log in to the registry: docker login ghcr.io",
                "Make sure your account has permission to pull the image.",
            ],
        )
    if "no space left" in lo:
        return (
            "Ran out of disk space while building or pulling images.",
            [
                "Free disk space (images cache: docker system df; clean: docker system prune -a --volumes).",
                "Retry: ./setup.sh",
            ],
        )
    if "permission denied" in lo and ("docker.sock" in lo or "/var/run/docker" in lo):
        return (
            "Docker daemon refused the connection (permissions).",
            [
                "Add your user to the 'docker' group: sudo usermod -aG docker $USER (then re-login).",
                "Or run ./setup.sh with sudo.",
            ],
        )
    if "cannot connect to the docker daemon" in lo:
        return (
            "Docker daemon is not running.",
            [
                "Start it: sudo systemctl start docker",
                "Verify: docker info",
            ],
        )
    if "port is already allocated" in lo or "address already in use" in lo:
        return (
            "A required port is already in use on this machine.",
            [
                "Stop the process using port 8001 / 7788 / 6080, or change the host port mapping in docker-compose.yml.",
                "To see listeners: sudo lsof -iTCP -sTCP:LISTEN -n -P",
            ],
        )
    return ("Docker Compose failed while building or starting the container.", [])


def _print_generic_failure(
    console: Console,
    *,
    stage: str,
    reason: str,
    hints: list[str] | None = None,
) -> None:
    """Render a red panel with a clear stage + reason + optional actionable hints."""
    lines = [
        f"[error]Step failed:[/error] {escape(stage)}",
        f"[error]Reason:[/error] {escape(reason or 'unknown')}",
    ]
    if hints:
        lines.append("")
        lines.append("[label]What to try:[/label]")
        for h in hints:
            lines.append(f"  • {escape(h)}")
    console.print(
        Panel(
            "\n".join(lines),
            border_style="red",
            expand=False,
            padding=(1, 2),
        ),
    )


def _print_cloud_verification_failure(console: Console, reason: str) -> None:
    _print_generic_failure(
        console,
        stage="Verifying connection to SellerClaw cloud",
        reason=reason,
        hints=[
            "Check your internet connection.",
            "Container logs: docker compose logs server",
            "Check status: ./setup.sh status",
            "Retry: ./setup.sh",
        ],
    )


def _print_start_failure(console: Console, base_url: str) -> None:
    _print_generic_failure(
        console,
        stage="Step 2 of 3 — starting SellerClaw",
        reason=f"The agent did not start responding at {base_url}/health in time.",
        hints=[
            "Check container logs: docker compose logs server",
            "Make sure port 8001 on 127.0.0.1 is not taken by another process.",
            "If the first build was killed/interrupted, rerun: ./setup.sh",
        ],
    )


def _print_compose_failure(
    console: Console,
    *,
    stage: str,
    output: str,
) -> None:
    title, hints = _diagnose_compose_failure(output)
    lines = [
        f"[error]Step failed:[/error] {escape(stage)}",
        f"[error]Reason:[/error] {escape(title)}",
    ]
    if hints:
        lines.append("")
        lines.append("[label]What to try:[/label]")
        for h in hints:
            lines.append(f"  • {escape(h)}")
    tail = [ln for ln in (output or "").strip().splitlines()[-12:] if ln.strip()]
    if tail:
        lines.append("")
        lines.append("[hint]Docker output (last lines):[/hint]")
        for ln in tail:
            lines.append(f"  [hint]{escape(ln)}[/hint]")
    console.print(
        Panel(
            "\n".join(lines),
            border_style="red",
            expand=False,
            padding=(1, 2),
        ),
    )


def _run_compose(
    agent_dir: Path,
    *compose_args: str,
    console: Console,
    status_text: str | None = None,
    extra_env: dict[str, str] | None = None,
    stage: str | None = None,
) -> int:
    prefix = _docker_compose_prefix()
    if prefix is None:
        console.print(
            "[error]Docker Compose v2 is required but not found.[/error]\n"
            "[hint]Install: https://docs.docker.com/compose/install/[/hint]",
        )
        return 1
    try:
        env_file_args = _compose_env_file_args(agent_dir)
    except RuntimeError as exc:
        console.print(f"[error]{escape(str(exc))}[/error]")
        return 1
    cmd = [*prefix, *env_file_args, *compose_args]
    label = status_text or f"$ {' '.join(cmd)}"
    run_env = os.environ.copy()
    if extra_env:
        run_env.update(extra_env)
    with Progress(
        SpinnerColumn(),
        TextColumn(f"[hint]{escape(label)}[/hint]"),
        console=console,
        transient=True,
    ):
        proc = subprocess.run(
            cmd,
            cwd=agent_dir,
            check=False,
            capture_output=True,
            text=True,
            env=run_env,
        )
    if proc.returncode != 0:
        output = (proc.stderr or "") + "\n" + (proc.stdout or "")
        _print_compose_failure(
            console,
            stage=stage or "docker compose " + " ".join(compose_args),
            output=output,
        )
    return int(proc.returncode)


# ---------------------------------------------------------------------------
# Agent HTTP helpers
# ---------------------------------------------------------------------------

def _extract_error_message(response: httpx.Response) -> str:
    """Best-effort human-readable error from an HTTP response."""
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, dict) and "message" in detail:
            return str(detail["message"])
    return f"HTTP {response.status_code}"


def wait_for_agent(base_url: str, console: Console, *, timeout_s: float = 120) -> bool:
    """Poll ``GET /health`` until the Agent Server responds."""
    deadline = time.monotonic() + timeout_s
    with Progress(
        SpinnerColumn(),
        TextColumn("[hint]Waiting for Agent Server…[/hint]"),
        console=console,
        transient=True,
    ):
        with httpx.Client(timeout=5.0) as client:
            while time.monotonic() < deadline:
                try:
                    r = client.get(f"{base_url}/health")
                    if r.status_code == 200:
                        return True
                except httpx.RequestError:
                    pass
                time.sleep(1)
    return False


def get_auth_status(base_url: str) -> dict[str, Any]:
    headers = _local_control_plane_auth_headers(base_url)
    with httpx.Client(timeout=15.0) as client:
        r = client.get(f"{base_url}/auth/status", headers=headers)
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict):
            msg = "Invalid /auth/status response"
            raise RuntimeError(msg)
        return body


def _get_health_snapshot(base_url: str) -> dict[str, Any]:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{base_url}/health")
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict):
            msg = "Invalid /health response"
            raise RuntimeError(msg)
        return body


def _wait_for_cloud_live(
    base_url: str,
    console: Console,
    *,
    timeout_s: float = 45.0,
) -> tuple[bool, str | None, bool]:
    """Verify the edge agent completed a real cloud round-trip after sign-in.

    Polls ``/health`` until ``session.connected`` is true AND ``ping_loop.last_success_at``
    is set — meaning the background loop registered an edge session and successfully
    heartbeated. Returns ``(ok, error_message, chat_sse_connected)``.
    """
    deadline = time.monotonic() + timeout_s
    last_error: str | None = None
    session_ok = False
    ping_ok = False
    chat_ok = False
    with Progress(
        SpinnerColumn(),
        TextColumn("[hint]Verifying connection to SellerClaw cloud…[/hint]"),
        console=console,
        transient=True,
    ):
        while time.monotonic() < deadline:
            try:
                snap = _get_health_snapshot(base_url)
            except (httpx.RequestError, httpx.HTTPStatusError, RuntimeError) as exc:
                last_error = str(exc)
                time.sleep(1.0)
                continue

            session = snap.get("session") or {}
            tasks = snap.get("tasks") or {}
            ping = tasks.get("ping_loop") or {}
            chat = tasks.get("chat_sse") or {}

            session_ok = bool(session.get("connected"))
            ping_ok = ping.get("last_success_at") is not None
            chat_ok = bool(chat.get("connected"))
            ping_error = ping.get("last_error")
            if isinstance(ping_error, str) and ping_error.strip():
                last_error = ping_error

            if session_ok and ping_ok:
                return True, None, chat_ok

            time.sleep(1.0)

    if not session_ok:
        reason = last_error or "edge session was not registered with the cloud in time."
    elif not ping_ok:
        reason = last_error or "no successful heartbeat with the cloud yet."
    else:
        reason = last_error or "unknown error."
    return False, reason, chat_ok


def _print_status(console: Console, base_url: str) -> int:
    try:
        s = get_auth_status(base_url)
    except httpx.ConnectError:
        console.print(
            f"[error]Agent is not reachable at {escape(base_url)}[/error]\n"
            "[hint]Start it first: ./setup.sh[/hint]",
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        console.print(f"[error]Could not reach agent: {escape(str(exc))}[/error]")
        return 1

    env_line = f"[hint]env: {escape(_active_env_label())}[/hint]"
    if s.get("connected"):
        console.print(
            Panel(
                f"[success]Connected[/success] as [label]{escape(s.get('user_name') or '?')}[/label] "
                f"({escape(s.get('user_email') or '?')})\n{env_line}",
                border_style="green",
                expand=False,
            ),
        )
    else:
        console.print(
            Panel(
                f"[warning]Not connected[/warning] to SellerClaw cloud.\n"
                f"{env_line}\n"
                "[hint]Run: ./setup.sh[/hint]",
                border_style="yellow",
                expand=False,
            ),
        )
    return 0


def _logout(console: Console, base_url: str) -> int:
    try:
        headers = _local_control_plane_auth_headers(base_url)
        with httpx.Client(timeout=15.0) as client:
            r = client.post(f"{base_url}/auth/disconnect", headers=headers)
            r.raise_for_status()
    except httpx.ConnectError:
        console.print(
            f"[error]Agent is not reachable at {escape(base_url)}[/error]\n"
            "[hint]Is it running? ./setup.sh[/hint]",
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        console.print(f"[error]Disconnect failed: {escape(str(exc))}[/error]")
        return 1
    console.print("[success]Disconnected from SellerClaw cloud.[/success]")
    return 0


# ---------------------------------------------------------------------------
# Auth flows
# ---------------------------------------------------------------------------

def _connect_password(console: Console, base_url: str, email: str, password: str) -> bool:
    """Return True on success, False on invalid credentials. Raise SystemExit on hard errors."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[hint]Signing in…[/hint]"),
        console=console,
        transient=True,
    ):
        headers = _local_control_plane_auth_headers(base_url)
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{base_url}/auth/connect",
                json={"email": email, "password": password},
                headers=headers,
            )
    if r.status_code == 401:
        console.print("[error]Invalid email or password.[/error]")
        return False
    if r.status_code == 502:
        console.print(
            "[error]We can't reach the SellerClaw cloud right now.[/error]\n"
            "[hint]Check your internet connection and try again. "
            "If the problem continues, please contact support.[/hint]",
        )
        raise SystemExit(1)
    if r.status_code >= 400:
        msg = _extract_error_message(r)
        console.print(f"[error]Sign-in failed: {escape(msg)}[/error]")
        raise SystemExit(1)
    r.raise_for_status()
    return True


def _device_flow(console: Console, base_url: str) -> None:
    with Progress(
        SpinnerColumn(),
        TextColumn("[hint]Preparing your sign-in link…[/hint]"),
        console=console,
        transient=True,
    ):
        try:
            headers = _local_control_plane_auth_headers(base_url)
            with httpx.Client(timeout=60.0) as client:
                start = client.post(f"{base_url}/auth/device/start", headers=headers)
        except httpx.ConnectError:
            console.print(
                "[error]SellerClaw is not running.[/error]\n"
                "[hint]Start it with: ./setup.sh[/hint]",
            )
            raise SystemExit(1) from None

    if start.status_code == 404:
        console.print(
            "[error]Browser sign-in isn't available right now.[/error]\n"
            "[hint]Please sign in with email and password instead.[/hint]",
        )
        raise SystemExit(1)
    if start.status_code == 502:
        console.print(
            "[error]We can't reach the SellerClaw cloud right now.[/error]\n"
            "[hint]Check your internet connection and try again.[/hint]",
        )
        raise SystemExit(1)
    if start.status_code >= 400:
        msg = _extract_error_message(start)
        console.print(f"[error]Could not start browser sign-in: {escape(msg)}[/error]")
        raise SystemExit(1)

    data = start.json()
    uri = str(data.get("verification_uri", "")).strip()
    user_code = str(data.get("user_code", "")).strip()
    device_code = str(data.get("device_code", "")).strip()
    interval = int(data.get("interval") or 5)

    console.print()
    console.print(
        Panel(
            f"[label]Your confirmation code:[/label]  [bold cyan]{escape(user_code)}[/bold cyan]\n\n"
            f"Open this link in your browser and confirm the code:\n[link={uri}]{escape(uri)}[/link]",
            title="[label]Sign in with your browser[/label]",
            border_style="cyan",
            expand=False,
            padding=(1, 3),
        ),
    )
    console.print()

    if uri:
        try:
            webbrowser.open(uri)
            console.print(
                "[hint]We've opened your browser. Confirm the code there, then come back here.[/hint]",
            )
        except Exception:  # noqa: BLE001
            console.print("[hint]Open the link above in your browser to continue.[/hint]")

    deadline = time.monotonic() + float(data.get("expires_in") or 900)
    with Progress(
        SpinnerColumn(),
        TextColumn("[hint]Waiting for you to confirm in the browser…[/hint]"),
        console=console,
        transient=True,
    ):
        poll_headers = _local_control_plane_auth_headers(base_url)
        with httpx.Client(timeout=30.0) as client:
            while time.monotonic() < deadline:
                try:
                    pr = client.get(
                        f"{base_url}/auth/device/poll",
                        params={"device_code": device_code},
                        headers=poll_headers,
                    )
                except httpx.ConnectError:
                    console.print("[error]Lost connection to SellerClaw while waiting.[/error]")
                    raise SystemExit(1) from None

                if pr.status_code >= 400:
                    msg = _extract_error_message(pr)
                    console.print(f"[error]Something went wrong: {escape(msg)}[/error]")
                    raise SystemExit(1)

                payload = pr.json()
                if payload.get("status") == "completed":
                    console.print()
                    console.print("[success]Signed in successfully![/success]")
                    return
                if payload.get("status") != "pending":
                    msg = _extract_error_message(pr)
                    console.print(f"[error]Sign-in failed: {escape(msg)}[/error]")
                    raise SystemExit(1)
                time.sleep(max(1, interval))

    console.print(
        "[error]Sign-in timed out.[/error]\n"
        "[hint]Run `./setup.sh` to try again.[/hint]",
    )
    raise SystemExit(1)


def _interactive_auth(console: Console, base_url: str) -> None:
    while True:
        method = _select_option(
            console,
            "How would you like to sign in?",
            [
                ("password", "Sign in with email and password"),
                ("browser", "Sign in with a browser link"),
            ],
            default_value="password",
        )
        console.print()

        if method == "password":
            while True:
                email = Prompt.ask("[label]Email[/label]")
                password = Prompt.ask("[label]Password[/label]", password=True)
                if _connect_password(console, base_url, email.strip(), password):
                    return
                console.print()
                if not _confirm(console, "Try again?", default=True):
                    break
                console.print()
            if not _confirm(console, "Pick a different sign-in method?", default=True):
                raise SystemExit(1)
            continue
        _device_flow(console, base_url)
        return


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_setup(console: Console) -> int:
    root = agent_root()
    base = agent_base_url()
    compose_file = root / "docker-compose.yml"
    if not compose_file.is_file():
        console.print(f"[error]docker-compose.yml not found at {escape(str(compose_file))}[/error]")
        return 1
    if _docker_compose_prefix() is None:
        console.print(
            "[error]Docker Compose v2 is required but not found.[/error]\n"
            "[hint]Install: https://docs.docker.com/compose/install/[/hint]",
        )
        return 1

    try:
        _require_agent_env()
        _compose_profile_env_file(root)
    except RuntimeError as exc:
        console.print(f"[error]{escape(str(exc))}[/error]")
        return 1

    env_label = _active_env_label()
    cloud_url = os.environ.get("SELLERCLAW_API_URL", "—")

    console.print()
    console.print(
        Panel(
            "[label]Welcome to SellerClaw[/label]\n"
            "[hint]This wizard will install SellerClaw on your computer and "
            "optionally connect it to your account.[/hint]\n"
            f"[hint]environment: {escape(env_label)}  ·  cloud: {escape(cloud_url)}[/hint]",
            border_style="cyan",
            expand=False,
            padding=(1, 3),
        ),
    )
    console.print()

    try:
        local_api_key = _ensure_local_api_key(root)
    except OSError as exc:
        console.print(f"[error]Cannot prepare local API key: {escape(str(exc))}[/error]")
        return 1

    console.print("[info]Step 1 of 3[/info]  Installing SellerClaw on your computer…")
    if (
        _run_compose(
            root,
            "up",
            "-d",
            "--build",
            "server",
            console=console,
            status_text="Preparing SellerClaw — this can take a minute the first time…",
            extra_env={"SELLERCLAW_LOCAL_API_KEY": local_api_key},
            stage="Step 1 of 3 — installing SellerClaw (docker compose up --build server)",
        )
        != 0
    ):
        return 1
    console.print("[success]  Done.[/success]\n")

    console.print("[info]Step 2 of 3[/info]  Starting SellerClaw…")
    if not wait_for_agent(base, console):
        _print_start_failure(console, base)
        return 1
    console.print("[success]  SellerClaw is running.[/success]\n")

    try:
        status = get_auth_status(base)
    except httpx.ConnectError:
        _print_start_failure(console, base)
        return 1
    except Exception as exc:  # noqa: BLE001
        _print_generic_failure(
            console,
            stage="Step 2 of 3 — verifying SellerClaw is reachable",
            reason=str(exc),
            hints=[
                "Check container logs: docker compose logs server",
                "Retry: ./setup.sh",
            ],
        )
        return 1

    if status.get("connected"):
        console.print(
            f"You are already signed in as [label]{escape(status.get('user_email', '?'))}[/label].",
        )
        if not _confirm(console, "Sign in with a different account?", default=False):
            _print_ready(console, connected=True)
            return 0
        console.print()

    console.print("[info]Step 3 of 3[/info]  Connect to SellerClaw")
    if not _confirm(console, "Connect to SellerClaw now?", default=True):
        console.print()
        _print_ready(console, connected=False)
        return 0

    console.print()
    _interactive_auth(console, base)

    connected = False
    try:
        st = get_auth_status(base)
        connected = bool(st.get("connected"))
        if connected:
            console.print(
                f"\n[success]Signed in[/success] as [label]{escape(st.get('user_name') or '?')}[/label] "
                f"({escape(st.get('user_email') or '?')})",
            )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[warning]Could not refresh sign-in status: {escape(str(exc))}[/warning]")

    if connected:
        ok, reason, chat_ok = _wait_for_cloud_live(base, console)
        if not ok:
            _print_cloud_verification_failure(console, reason or "unknown error")
            return 1
        if not chat_ok:
            console.print(
                "[warning]Live chat stream is not connected yet — it should come up shortly.[/warning]",
            )

    console.print()
    _print_ready(console, connected=connected)
    return 0


def _print_ready(console: Console, *, connected: bool) -> None:
    if connected:
        console.print(
            Panel(
                "[success]SellerClaw is running and connected to your account.[/success]\n\n"
                "To stop:    [info]./setup.sh stop[/info]",
                border_style="green",
                expand=False,
                padding=(1, 3),
            ),
        )
        return
    console.print(
        Panel(
            "[warning]SellerClaw is installed but not connected to your account.[/warning]\n\n"
            "To connect: [info]./setup.sh[/info]\n"
            "To stop:    [info]./setup.sh stop[/info]",
            border_style="yellow",
            expand=False,
            padding=(1, 3),
        ),
    )


def cmd_start(console: Console) -> int:
    root = agent_root()
    try:
        local_api_key = _ensure_local_api_key(root)
    except OSError as exc:
        console.print(f"[error]Cannot prepare local API key: {escape(str(exc))}[/error]")
        return 1
    console.print("Starting SellerClaw…")
    rc = _run_compose(
        root,
        "up",
        "-d",
        "--build",
        "server",
        console=console,
        status_text="Starting SellerClaw…",
        extra_env={"SELLERCLAW_LOCAL_API_KEY": local_api_key},
        stage="start — docker compose up --build server",
    )
    if rc != 0:
        return rc
    base = agent_base_url()
    if not wait_for_agent(base, console, timeout_s=60):
        _print_start_failure(console, base)
        return 1
    console.print("[success]SellerClaw is running.[/success]")
    return 0


def cmd_stop(console: Console) -> int:
    console.print("Stopping SellerClaw…")
    rc = _run_compose(
        agent_root(),
        "down",
        console=console,
        status_text="Stopping SellerClaw…",
        stage="stop — docker compose down",
    )
    if rc == 0:
        console.print("[success]SellerClaw stopped.[/success]")
    return rc


def cmd_login(console: Console) -> int:
    base = agent_base_url()
    if not wait_for_agent(base, console, timeout_s=15):
        console.print(
            f"[error]Agent is not reachable at {escape(base)}[/error]\n"
            "[hint]Start it first: ./setup.sh[/hint]",
        )
        return 1
    _interactive_auth(console, base)
    ok, reason, chat_ok = _wait_for_cloud_live(base, console)
    if not ok:
        _print_cloud_verification_failure(console, reason or "unknown error")
        return 1
    if not chat_ok:
        console.print(
            "[warning]Live chat stream is not connected yet — it should come up shortly.[/warning]",
        )
    return _print_status(console, base)


def cmd_help(console: Console) -> int:
    console.print()
    console.print("[label]sellerclaw-agent[/label] [hint]— CLI for SellerClaw edge agent[/hint]\n")

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Command", style="info")
    tbl.add_column("Description")
    tbl.add_row("setup", "Install the server and connect to your account  [hint](default)[/hint]")
    tbl.add_row("start", "Start the SellerClaw container")
    tbl.add_row("stop", "Stop the SellerClaw container")
    tbl.add_row("status", "Show cloud connection status")
    tbl.add_row("login", "Connect to your account (server must be running)")
    tbl.add_row("logout", "Disconnect from your account")
    tbl.add_row("help", "Show this help")
    console.print(tbl)

    console.print()
    return 0


def main() -> None:
    console = _console()
    cmd = parse_command(sys.argv[1:])
    if cmd == "help":
        raise SystemExit(cmd_help(console))
    if cmd == "setup":
        raise SystemExit(cmd_setup(console))
    if cmd == "start":
        raise SystemExit(cmd_start(console))
    if cmd == "stop":
        raise SystemExit(cmd_stop(console))
    if cmd == "status":
        raise SystemExit(_print_status(console, agent_base_url()))
    if cmd == "login":
        raise SystemExit(cmd_login(console))
    if cmd == "logout":
        raise SystemExit(_logout(console, agent_base_url()))
    console.print(
        f"[error]Unknown command: {escape(cmd)}[/error]\n"
        "[hint]Run: ./setup.sh help[/hint]",
    )
    raise SystemExit(2)
