"""The Claude Code CLI on this machine: install it on demand, ask it whether it is signed in.

Claude Code is Anthropic's own program and is not baked into our public image: ``connect claude``
fetches it with Anthropic's official installer into the data volume (so ``sellerclaw-agent
update`` keeps it), at a pinned version with self-updating off. The owner signs in through Claude
Code's own ``auth login`` in their terminal — the code they paste goes straight to it. This module
never reads Claude Code's credentials; it only asks ``claude auth status``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sellerclaw_agent.subscription.state import subscription_dir

#: Version installed by ``connect claude``. Raised deliberately, after checking the new build with
#: the pinned OpenClaw (its Claude CLI backend speaks Claude Code's stdio protocol).
CLAUDE_CODE_VERSION = "2.1.311"

#: Anthropic's official installer (``curl -fsSL https://claude.ai/install.sh | bash`` redirects
#: here). It downloads the build, verifies its SHA-256 against Anthropic's manifest, then installs.
CLAUDE_BOOTSTRAP_URL = "https://downloads.claude.ai/claude-code-releases/bootstrap.sh"

_DOWNLOAD_ATTEMPTS = 3
_INSTALL_ATTEMPTS = 2
_STATUS_TIMEOUT_SECONDS = 20.0

#: ``curl`` settings for the installer's own downloads (it runs plain ``curl -fsSL``). From inside
#: Docker the first connection to downloads.claude.ai sometimes hangs for minutes and then fails,
#: while the next one connects at once: give up on a dead connection quickly and try again.
_INSTALLER_CURLRC = "connect-timeout = 20\nretry = 4\nretry-connrefused\n"


def claude_home(data_dir: Path) -> Path:
    """Where the installer puts the binary (its ``$HOME``), on the data volume."""
    return subscription_dir(data_dir) / "claude-home"


def claude_config_dir(data_dir: Path) -> Path:
    """Claude Code's own config dir (``CLAUDE_CONFIG_DIR``): its sign-in and session history."""
    return subscription_dir(data_dir) / "claude"


def claude_binary(data_dir: Path) -> Path:
    """The real binary the installer links; ``~/.local/bin/claude`` in the image wraps it."""
    return claude_home(data_dir) / ".local" / "bin" / "claude"


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


def _download_bootstrap(target: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(CLAUDE_BOOTSTRAP_URL, timeout=60) as response:  # noqa: S310 - fixed https URL
                target.write_bytes(response.read())
            return
        except OSError as exc:  # URLError is an OSError; a flaky route usually works the next time
            last_error = exc
            time.sleep(2 * attempt)
    raise ClaudeInstallError(
        f"Could not download Claude Code from downloads.claude.ai ({last_error}). "
        "Check this computer's internet connection and run the command again."
    )


def install(data_dir: Path, *, version: str = CLAUDE_CODE_VERSION) -> Path:
    """Install Claude Code ``version`` on the data volume (no-op when it is already there)."""
    binary = claude_binary(data_dir)
    if binary.exists():
        return binary
    home = claude_home(data_dir)
    home.mkdir(parents=True, exist_ok=True)
    claude_config_dir(data_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="claude-bootstrap-") as tmp:
        script = Path(tmp) / "bootstrap.sh"
        _download_bootstrap(script)
        (Path(tmp) / ".curlrc").write_text(_INSTALLER_CURLRC, encoding="utf-8")
        env = claude_env(data_dir)
        env["HOME"] = str(home)
        env["CURL_HOME"] = tmp
        tail: list[str] = []
        for _attempt in range(_INSTALL_ATTEMPTS):
            result = subprocess.run(  # noqa: S603 - Anthropic's installer, fixed arguments
                ["bash", str(script), version],
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if result.returncode == 0 and binary.exists():
                return binary
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
    raise ClaudeInstallError("Claude Code installation failed: " + " ".join(tail))


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
