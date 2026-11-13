"""Installing Claude Code on demand survives the flaky first connection seen from Docker."""

from __future__ import annotations

from pathlib import Path

import pytest
from sellerclaw_agent.subscription import claude_cli

pytestmark = pytest.mark.unit

# A stand-in for Anthropic's installer: notes the curl settings it was given, fails while the
# remaining-failures counter is above zero, then "installs" the binary under $HOME like the real one.
_FAKE_BOOTSTRAP = """#!/bin/bash
set -e
state="$FAKE_INSTALLER_STATE"
cp "$CURL_HOME/.curlrc" "$state/curlrc-seen"
echo "$1" >> "$state/versions"
left=$(cat "$state/failures-left")
if [ "$left" -gt 0 ]; then
  echo $((left - 1)) > "$state/failures-left"
  echo "curl: (28) Failed to connect to downloads.claude.ai port 443" >&2
  exit 1
fi
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\\n' > "$HOME/.local/bin/claude"
chmod +x "$HOME/.local/bin/claude"
"""


@pytest.fixture
def installer_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()

    monkeypatch.setenv("FAKE_INSTALLER_STATE", str(state))
    monkeypatch.setattr(
        claude_cli, "_download_bootstrap", lambda target: target.write_text(_FAKE_BOOTSTRAP, encoding="utf-8")
    )
    return state


@pytest.mark.parametrize(
    ("failures", "attempts_made"),
    [
        pytest.param(0, 1, id="first-run-works"),
        pytest.param(1, 2, id="flaky-first-connection-then-works"),
    ],
)
def test_install_retries_the_installer_with_patient_curl(
    tmp_path: Path, installer_state: Path, failures: int, attempts_made: int
) -> None:
    (installer_state / "failures-left").write_text(str(failures))
    data_dir = tmp_path / "data"

    binary = claude_cli.install(data_dir)

    assert binary == data_dir / "ai-subscription" / "claude-home" / ".local" / "bin" / "claude"
    assert binary.exists()
    assert (installer_state / "versions").read_text().split() == [claude_cli.CLAUDE_CODE_VERSION] * attempts_made
    assert (installer_state / "curlrc-seen").read_text() == "connect-timeout = 20\nretry = 4\nretry-connrefused\n"
    assert (data_dir / "ai-subscription" / "claude").is_dir()


def test_install_gives_up_with_the_installers_last_words(tmp_path: Path, installer_state: Path) -> None:
    (installer_state / "failures-left").write_text("5")

    with pytest.raises(
        claude_cli.ClaudeInstallError,
        match=r"^Claude Code installation failed: curl: \(28\) Failed to connect to downloads\.claude\.ai port 443$",
    ):
        claude_cli.install(tmp_path / "data")

    assert (installer_state / "versions").read_text().split() == [claude_cli.CLAUDE_CODE_VERSION] * 2
    assert (installer_state / "failures-left").read_text().strip() == "3"


@pytest.mark.parametrize(
    "installed",
    [
        pytest.param(True, id="installed"),
        pytest.param(False, id="never-installed"),
    ],
)
def test_signing_out_forgets_the_last_limits_reading(tmp_path: Path, installed: bool) -> None:
    """A "limit reached" seen on one account must not pause the next sign-in, maybe another account."""
    data_dir = tmp_path / "data"
    limits = claude_cli.limits_path(data_dir)
    limits.parent.mkdir(parents=True)
    limits.write_text('{"status": "rejected", "windows": [{"window": "5h", "used_percent": 100}]}')
    binary = claude_cli.claude_binary(data_dir)
    if installed:
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\necho "$@" > "$(dirname "$0")/called-with"\n')
        binary.chmod(0o755)

    claude_cli.logout(data_dir)

    assert limits.parent.is_dir()
    assert not limits.exists()
    if installed:
        assert (binary.parent / "called-with").read_text().strip() == "auth logout"
