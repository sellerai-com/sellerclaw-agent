"""Terminal onboarding CLI for SellerClaw Agent (talks to local Agent Server only)."""

from __future__ import annotations

import json
import os
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


def _console() -> Console:
    return Console(theme=_THEME)


def agent_root() -> Path:
    """Directory containing ``docker-compose.yml`` (sellerclaw-agent package parent)."""
    return Path(__file__).resolve().parent.parent


def agent_base_url() -> str:
    """Base URL of the Agent Server (mapped host port in docker-compose)."""
    return (os.environ.get("SELLERCLAW_AGENT_URL") or "http://127.0.0.1:8001").rstrip("/")


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
    """Human-readable label for the current AGENT_ENV (or 'local')."""
    return os.environ.get("AGENT_ENV", "").strip() or "local"


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


def _run_compose(
    agent_dir: Path,
    *compose_args: str,
    console: Console,
    status_text: str | None = None,
) -> int:
    prefix = _docker_compose_prefix()
    if prefix is None:
        console.print(
            "[error]Docker Compose v2 is required but not found.[/error]\n"
            "[hint]Install: https://docs.docker.com/compose/install/[/hint]",
        )
        return 1
    cmd = [*prefix, *compose_args]
    label = status_text or f"$ {' '.join(cmd)}"
    with Progress(
        SpinnerColumn(),
        TextColumn(f"[hint]{escape(label)}[/hint]"),
        console=console,
        transient=True,
    ):
        proc = subprocess.run(cmd, cwd=agent_dir, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-30:]
        if tail:
            console.print("[error]Docker Compose failed:[/error]")
            for line in tail:
                console.print(f"  [hint]{escape(line)}[/hint]")
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
    """Poll ``GET /auth/status`` until the Agent Server responds."""
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
                    r = client.get(f"{base_url}/auth/status")
                    if r.status_code == 200:
                        return True
                except httpx.RequestError:
                    pass
                time.sleep(1)
    return False


def get_auth_status(base_url: str) -> dict[str, Any]:
    with httpx.Client(timeout=15.0) as client:
        r = client.get(f"{base_url}/auth/status")
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict):
            msg = "Invalid /auth/status response"
            raise RuntimeError(msg)
        return body


def _print_status(console: Console, base_url: str) -> int:
    try:
        s = get_auth_status(base_url)
    except httpx.ConnectError:
        console.print(
            f"[error]Agent is not reachable at {escape(base_url)}[/error]\n"
            "[hint]Start it first: sellerclaw-agent start[/hint]",
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
                "[hint]Run: sellerclaw-agent login[/hint]",
                border_style="yellow",
                expand=False,
            ),
        )
    return 0


def _logout(console: Console, base_url: str) -> int:
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(f"{base_url}/auth/disconnect")
            r.raise_for_status()
    except httpx.ConnectError:
        console.print(
            f"[error]Agent is not reachable at {escape(base_url)}[/error]\n"
            "[hint]Is it running? sellerclaw-agent start[/hint]",
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
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{base_url}/auth/connect",
                json={"email": email, "password": password},
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
            with httpx.Client(timeout=60.0) as client:
                start = client.post(f"{base_url}/auth/device/start")
        except httpx.ConnectError:
            console.print(
                "[error]SellerClaw is not running.[/error]\n"
                "[hint]Start it with: sellerclaw-agent start[/hint]",
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
        with httpx.Client(timeout=30.0) as client:
            while time.monotonic() < deadline:
                try:
                    pr = client.get(f"{base_url}/auth/device/poll", params={"device_code": device_code})
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
        "[hint]Run `sellerclaw-agent login` to try again.[/hint]",
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

    env_label = _active_env_label()
    cloud_url = os.environ.get("SELLERCLAW_API_URL", "—")

    console.print()
    console.print(
        Panel(
            "[label]Welcome to SellerClaw[/label]\n"
            "[hint]This wizard will install SellerClaw on your computer and sign you in.[/hint]\n"
            f"[hint]environment: {escape(env_label)}  ·  cloud: {escape(cloud_url)}[/hint]",
            border_style="cyan",
            expand=False,
            padding=(1, 3),
        ),
    )
    console.print()

    console.print("[info]Step 1 of 3[/info]  Installing SellerClaw on your computer…")
    if (
        _run_compose(
            root,
            "up",
            "-d",
            "--build",
            console=console,
            status_text="Preparing SellerClaw — this can take a minute the first time…",
        )
        != 0
    ):
        console.print(
            "\n[hint]If this keeps failing, please send us the error above so we can help.[/hint]",
        )
        return 1
    console.print("[success]  Done.[/success]\n")

    console.print("[info]Step 2 of 3[/info]  Starting SellerClaw…")
    if not wait_for_agent(base, console):
        console.print(
            "[error]SellerClaw did not start in time.[/error]\n"
            "[hint]Please try running setup again. If the problem continues, contact support.[/hint]",
        )
        return 1
    console.print("[success]  SellerClaw is running.[/success]\n")

    try:
        status = get_auth_status(base)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[error]{escape(str(exc))}[/error]")
        return 1

    if status.get("connected"):
        console.print(
            f"You are already signed in as [label]{escape(status.get('user_email', '?'))}[/label].",
        )
        if not _confirm(console, "Sign in with a different account?", default=False):
            _print_ready(console, base)
            return 0
        console.print()

    console.print("[info]Step 3 of 3[/info]  Sign in to your SellerClaw account")
    _interactive_auth(console, base)

    try:
        st = get_auth_status(base)
        if st.get("connected"):
            console.print(
                f"\n[success]Signed in[/success] as [label]{escape(st.get('user_name') or '?')}[/label] "
                f"({escape(st.get('user_email') or '?')})",
            )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[warning]Could not refresh sign-in status: {escape(str(exc))}[/warning]")

    console.print()
    _print_ready(console, base)
    return 0


def _print_ready(console: Console, base_url: str) -> None:
    console.print(
        Panel(
            f"[success]You're all set![/success]\n\n"
            f"Open SellerClaw:  [link={base_url}/admin]{base_url}/admin[/link]\n"
            f"Stop SellerClaw:  [info]sellerclaw-agent stop[/info]",
            border_style="green",
            expand=False,
            padding=(1, 3),
        ),
    )


def cmd_start(console: Console) -> int:
    return _run_compose(agent_root(), "up", "-d", "--build", console=console)


def cmd_stop(console: Console) -> int:
    return _run_compose(agent_root(), "down", console=console)


def cmd_login(console: Console) -> int:
    base = agent_base_url()
    if not wait_for_agent(base, console, timeout_s=15):
        console.print(
            f"[error]Agent is not reachable at {escape(base)}[/error]\n"
            "[hint]Start it first: sellerclaw-agent start[/hint]",
        )
        return 1
    _interactive_auth(console, base)
    return _print_status(console, base)


def cmd_help(console: Console) -> int:
    console.print()
    console.print("[label]sellerclaw-agent[/label] [hint]— CLI for SellerClaw edge agent[/hint]\n")

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Command", style="info")
    tbl.add_column("Description")
    tbl.add_row("setup", "Start Docker stack, sign in, show admin URL  [hint](default)[/hint]")
    tbl.add_row("start", "docker compose up -d --build")
    tbl.add_row("stop", "docker compose down")
    tbl.add_row("status", "Show cloud connection status")
    tbl.add_row("login", "Sign in (server must be running)")
    tbl.add_row("logout", "Clear stored cloud credentials on the agent")
    tbl.add_row("help", "Show this help")
    console.print(tbl)

    console.print(
        "\n[hint]Env:[/hint] SELLERCLAW_AGENT_URL [hint](default http://127.0.0.1:8001)[/hint]",
    )
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
        "[hint]Run: sellerclaw-agent help[/hint]",
    )
    raise SystemExit(2)
