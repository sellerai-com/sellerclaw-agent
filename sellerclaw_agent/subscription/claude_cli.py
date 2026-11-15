"""The Claude Code CLI on this machine: install it on demand, ask it whether it is signed in.

Claude Code is Anthropic's own program and is not baked into our public image: ``connect claude``
downloads the pinned build from Anthropic's release bucket into the data volume (so ``sellerclaw-agent
update`` keeps it), checks it against the SHA-256 in Anthropic's manifest, and lays it out the way
Anthropic's installer does (``versions/<version>`` plus the ``.local/bin/claude`` link), with
self-updating off. The owner signs in through Claude Code's own ``auth login`` in their terminal —
the code they paste goes straight to it. This module never reads Claude Code's credentials; it only
asks ``claude auth status``.

**Why not Anthropic's installer script.** It downloads the *latest* build first and then has it
install the pinned one, which downloads again — about 660 MB on Linux — and its steps give up on
their own short timeouts. Live on staging (14.11.2026) a slow connection (~100 KB/s) could never
finish. Fetching the pinned build alone is one ~290 MB download that resumes where a dropped
connection stopped and reports its progress.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from sellerclaw_agent.subscription.state import subscription_dir

#: Version installed by ``connect claude``. Raised deliberately, after checking the new build with
#: the pinned OpenClaw (its Claude CLI backend speaks Claude Code's stdio protocol).
CLAUDE_CODE_VERSION = "2.1.311"

#: Anthropic's release bucket (what ``claude.ai/install.sh`` downloads from): ``<version>/manifest.json``
#: lists each platform's build with its SHA-256 and size; the build is ``<version>/<platform>/<binary>``.
CLAUDE_RELEASES_URL = "https://downloads.claude.ai/claude-code-releases"

#: Tries at the download; each one resumes where the last stopped, so a flaky line still gets there.
_DOWNLOAD_ATTEMPTS = 8
#: A connection that is not up in 20 s will not come up; no byte for 60 s means a dead one.
_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=20.0)
_CHUNK_BYTES = 1 << 20
_STATUS_TIMEOUT_SECONDS = 20.0

#: ``(bytes so far, total bytes)`` — how ``connect claude`` draws the download's progress.
ProgressCallback = Callable[[int, int], None]


def claude_home(data_dir: Path) -> Path:
    """Where the installer puts the binary (its ``$HOME``), on the data volume."""
    return subscription_dir(data_dir) / "claude-home"


def claude_config_dir(data_dir: Path) -> Path:
    """Claude Code's own config dir (``CLAUDE_CONFIG_DIR``): its sign-in and session history."""
    return subscription_dir(data_dir) / "claude"


def claude_binary(data_dir: Path) -> Path:
    """The link to the real binary; ``~/.local/bin/claude`` in the image wraps it."""
    return claude_home(data_dir) / ".local" / "bin" / "claude"


def claude_versions_dir(data_dir: Path) -> Path:
    """Where the builds live, one file per version — the layout of Anthropic's own installer."""
    return claude_home(data_dir) / ".local" / "share" / "claude" / "versions"


def limits_path(data_dir: Path) -> Path:
    """The last 5-hour/weekly reading the ``claude`` wrapper took from Claude Code's own output."""
    return subscription_dir(data_dir) / "claude-limits.json"


def claude_env(data_dir: Path) -> dict[str, str]:
    """Environment every call to Claude Code runs with: its config on the volume, no self-updates."""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(claude_config_dir(data_dir))
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


class ClaudeInstallError(RuntimeError):
    """Claude Code could not be installed (network, region, disk)."""


def is_installed(data_dir: Path) -> bool:
    return claude_binary(data_dir).exists()


@dataclass(frozen=True)
class ClaudeBuild:
    """One platform's build of one version, as Anthropic's manifest lists it."""

    url: str
    sha256: str
    size: int


def _platform() -> str:
    """Anthropic's name for this machine's build (the agent image is Linux, glibc or musl)."""
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        arch = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise ClaudeInstallError(f"Claude Code has no build for this processor ({machine}).")
    musl = Path(f"/lib/libc.musl-{'x86_64' if arch == 'x64' else 'aarch64'}.so.1").exists()
    return f"linux-{arch}-musl" if musl else f"linux-{arch}"


def _build(client: httpx.Client, version: str) -> ClaudeBuild:
    target = _platform()
    response = client.get(f"{CLAUDE_RELEASES_URL}/{version}/manifest.json")
    response.raise_for_status()
    try:
        entry = response.json()["platforms"][target]
        build = ClaudeBuild(
            url=f"{CLAUDE_RELEASES_URL}/{version}/{target}/{entry.get('binary') or 'claude'}",
            sha256=str(entry["checksum"]).lower(),
            size=int(entry["size"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ClaudeInstallError(f"Anthropic's manifest for Claude Code {version} has no {target} build.") from exc
    if build.url.endswith(".zst"):
        raise ClaudeInstallError(f"Claude Code {version} ships only a compressed {target} build.")
    return build


def _download(client: httpx.Client, build: ClaudeBuild, part: Path, on_progress: ProgressCallback | None) -> None:
    """Fetch ``build`` into ``part``, continuing a partial file. Raises when the line drops."""
    have = part.stat().st_size if part.exists() else 0
    if have > build.size:
        part.unlink()
        have = 0
    if have == build.size:
        return
    headers = {"Range": f"bytes={have}-"} if have else {}
    with client.stream("GET", build.url, headers=headers) as response:
        response.raise_for_status()
        if have and response.status_code != 206:
            have = 0  # the server sent the whole file instead of the rest
        with part.open("ab" if have else "wb") as out:
            for chunk in response.iter_bytes(_CHUNK_BYTES):
                out.write(chunk)
                have += len(chunk)
                if on_progress is not None:
                    on_progress(have, build.size)
    if have < build.size:
        raise httpx.ReadError(f"the download stopped at {have} of {build.size} bytes")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def install(
    data_dir: Path,
    *,
    version: str = CLAUDE_CODE_VERSION,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Install Claude Code ``version`` on the data volume (no-op when it is already there)."""
    binary = claude_binary(data_dir)
    if binary.exists():
        return binary
    claude_config_dir(data_dir).mkdir(parents=True, exist_ok=True)
    versions = claude_versions_dir(data_dir)
    versions.mkdir(parents=True, exist_ok=True)
    part = versions / f"{version}.part"
    last_error: Exception | None = None
    build: ClaudeBuild | None = None
    with httpx.Client(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
            try:
                build = build or _build(client, version)
                _download(client, build, part, on_progress)
                break
            except (httpx.HTTPError, OSError) as exc:  # a flaky route usually works on the next try
                last_error = exc
                time.sleep(min(2 * attempt, 10))
        else:
            raise ClaudeInstallError(
                f"Could not download Claude Code from downloads.claude.ai ({last_error or 'no answer'}). "
                "Check this computer's internet connection and run the command again — "
                "it continues where it stopped."
            )
    assert build is not None
    if _sha256(part) != build.sha256:
        part.unlink(missing_ok=True)
        raise ClaudeInstallError(
            "The downloaded Claude Code does not match Anthropic's checksum. Run the command again."
        )
    part.chmod(0o755)
    target = versions / version
    part.replace(target)
    binary.parent.mkdir(parents=True, exist_ok=True)
    link = binary.with_name(f".{binary.name}.new")
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    link.replace(binary)
    return binary


@dataclass(frozen=True)
class ClaudeAuthStatus:
    """What ``claude auth status --json`` says — never the account's identity beyond the plan."""

    logged_in: bool
    auth_method: str | None = None
    subscription_type: str | None = None

    @property
    def on_subscription(self) -> bool:
        """Signed in with a Claude plan (not an API key billed per use)."""
        return self.logged_in and self.auth_method == "claude.ai"


def auth_status(data_dir: Path) -> ClaudeAuthStatus:
    """Ask Claude Code whether it is signed in. ``logged_in=False`` when it is missing or fails."""
    binary = claude_binary(data_dir)
    if not binary.exists():
        return ClaudeAuthStatus(logged_in=False)
    try:
        result = subprocess.run(  # noqa: S603 - our pinned install, fixed arguments
            [str(binary), "auth", "status", "--json"],
            env=claude_env(data_dir),
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_SECONDS,
            check=False,
        )
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ClaudeAuthStatus(logged_in=False)
    if not isinstance(data, dict):
        return ClaudeAuthStatus(logged_in=False)
    method = data.get("authMethod")
    plan = data.get("subscriptionType")
    return ClaudeAuthStatus(
        logged_in=data.get("loggedIn") is True,
        auth_method=method if isinstance(method, str) else None,
        subscription_type=plan if isinstance(plan, str) and plan else None,
    )


def login_interactive(data_dir: Path) -> int:
    """Run Claude Code's own sign-in in the owner's terminal; returns its exit code.

    The terminal is handed over as is: the link Claude Code prints and the code the owner pastes
    are between them and Anthropic — nothing is captured or logged here.
    """
    return subprocess.call(  # noqa: S603 - our pinned install, fixed arguments
        [str(claude_binary(data_dir)), "auth", "login", "--claudeai"],
        env=claude_env(data_dir),
    )


def logout(data_dir: Path) -> None:
    """Sign Claude Code out on this machine (best effort: a missing install is already signed out).

    The account's last limits reading goes too: a "limit reached" left behind would pause the next
    sign-in — maybe another account — until a reset that is not its own.
    """
    limits_path(data_dir).unlink(missing_ok=True)
    binary = claude_binary(data_dir)
    if not binary.exists():
        return
    subprocess.run(  # noqa: S603 - our pinned install, fixed arguments
        [str(binary), "auth", "logout"],
        env=claude_env(data_dir),
        capture_output=True,
        timeout=_STATUS_TIMEOUT_SECONDS,
        check=False,
    )


def uninstall(data_dir: Path) -> None:
    """Remove the binary from the volume (the owner's sign-in goes with ``logout``)."""
    shutil.rmtree(claude_home(data_dir), ignore_errors=True)
