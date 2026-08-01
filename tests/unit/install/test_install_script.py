"""Contract tests for ``install.sh`` — the one-command installer.

The script is what a stranger pipes into ``sh`` from the README, so the parts that are
expensive to get wrong (which image, which ports, which volume, which cloud, what happens
on a re-run) are pinned here. Docker is stubbed: the tests assert on the exact commands
the installer would issue, never on a real container.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_SH = REPO_ROOT / "install.sh"

# Only what the script legitimately needs. Docker stays out unless a test stubs it, so a
# "docker is missing" path cannot be masked by the host's real binary.
SYSBIN_TOOLS = (
    "awk",
    "bash",
    "cat",
    "chmod",
    "cp",
    "dash",
    "grep",
    "id",
    "mkdir",
    "mktemp",
    "mv",
    "printf",
    "rm",
    "sed",
    "sh",
    "sleep",
    "true",
    "false",
)


def _populate_sysbin(sysbin: Path) -> None:
    sysbin.mkdir(exist_ok=True)
    for name in SYSBIN_TOOLS:
        for base in (Path("/usr/bin"), Path("/bin")):
            src = base / name
            if src.exists():
                link = sysbin / name
                if not link.exists():
                    link.symlink_to(src)
                break


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    # Absolute shebang: the sandbox PATH excludes /bin and /usr/bin, so `/usr/bin/env bash`
    # would not resolve.
    path = bin_dir / name
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)


@dataclass
class Sandbox:
    work: Path
    bin: Path
    sysbin: Path
    home: Path
    bin_target: Path
    docker_log: Path

    @property
    def env(self) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}:{self.sysbin}",
            "HOME": str(self.home),
            "LANG": "C",
            "LC_ALL": "C",
            "SETUP_MEMINFO_PATH": str(self.work / "meminfo"),
            "SELLERCLAW_BIN_DIR": str(self.bin_target),
            "SELLERCLAW_HOME": str(self.home / ".sellerclaw-agent"),
            "DOCKER_LOG": str(self.docker_log),
        }

    @property
    def wrapper(self) -> Path:
        return self.bin_target / "sellerclaw-agent"

    def docker_calls(self) -> list[str]:
        if not self.docker_log.exists():
            return []
        return [line for line in self.docker_log.read_text().splitlines() if line]


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(INSTALL_SH, work / "install.sh")
    (work / "install.sh").chmod(0o755)
    (work / "meminfo").write_text(f"MemTotal: {8 * 1024 * 1024} kB\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "uname", 'case "${1:-}" in -m) echo x86_64 ;; *) echo Linux ;; esac')

    sysbin = tmp_path / "sysbin"
    _populate_sysbin(sysbin)

    home = tmp_path / "home"
    home.mkdir()
    bin_target = tmp_path / "target-bin"
    return Sandbox(
        work=work,
        bin=bin_dir,
        sysbin=sysbin,
        home=home,
        bin_target=bin_target,
        docker_log=tmp_path / "docker.log",
    )


def _install_docker_stub(
    sandbox: Sandbox,
    *,
    existing_container: str = "",
    connected: bool = False,
    old_image: bool = False,
) -> None:
    """Stub `docker` that records every call and answers the handful of queries used."""
    status_line = "Connected as Tester" if connected else "Not connected"
    entry_point_branch = "exit 1" if old_image else "exit 0"
    body = textwrap.dedent(f"""
        printf '%s\\n' "$*" >> "$DOCKER_LOG"
        case "${{1:-}}" in
            ps)
                if [[ "$*" == *"-a"* ]]; then printf '%s\\n' "{existing_container}"; fi
                exit 0
                ;;
            inspect)
                if [[ "$*" == *"State.Running"* ]]; then echo true; fi
                exit 0
                ;;
            exec)
                if [[ "$*" == *"import sellerclaw_agent.__main__"* ]]; then {entry_point_branch}; fi
                if [[ "$*" == *"sellerclaw_agent status"* ]]; then echo "{status_line}"; fi
                exit 0
                ;;
            volume)
                if [[ "${{2:-}}" == "inspect" ]]; then exit 0; fi
                exit 0
                ;;
            *) exit 0 ;;
        esac
    """).strip()
    _write_stub(sandbox.bin, "docker", body)


def _run(
    sandbox: Sandbox,
    args: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**sandbox.env, **(env_overrides or {})}
    return subprocess.run(
        ["/bin/sh", str(sandbox.work / "install.sh"), *args],
        cwd=sandbox.work,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=60,
        # A new session has no controlling terminal, so the installer's `/dev/tty` questions
        # cannot be answered — which is exactly the unattended install these tests describe.
        # Without it the child inherits the developer's terminal when the suite is run from a
        # shell: the prompt lands in their console and the run blocks until the timeout.
        start_new_session=True,
    )


def _docker_run_line(sandbox: Sandbox, result: subprocess.CompletedProcess[str]) -> str:
    for line in result.stdout.splitlines():
        if line.startswith("+ docker run"):
            return line
    for call in sandbox.docker_calls():
        if call.startswith("run "):
            return f"+ docker {call}"
    msg = f"no docker run command found in:\n{result.stdout}\n{result.stderr}"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# What the installer would actually run
# ---------------------------------------------------------------------------

def test_dry_run_starts_prebuilt_image_without_building(sandbox: Sandbox) -> None:
    result = _run(sandbox, ["--dry-run"])

    assert result.returncode == 0, result.stderr
    assert "+ docker pull ghcr.io/sellerai-com/sellerclaw-agent:latest" in result.stdout
    assert "build" not in result.stdout
    assert sandbox.docker_calls() == []


def test_dry_run_container_arguments(sandbox: Sandbox) -> None:
    result = _run(sandbox, ["--dry-run"])
    run_line = _docker_run_line(sandbox, result)

    assert "--name sellerclaw-agent" in run_line
    assert "--restart unless-stopped" in run_line
    # Loopback only: the agent never needs inbound traffic.
    assert "-p 127.0.0.1:8001:8001" in run_line
    assert "-p 127.0.0.1:6080:6080" in run_line
    assert "-v sellerclaw-agent-data:/data" in run_line
    assert "-e SELLERCLAW_DATA_DIR=/data" in run_line
    assert run_line.endswith("ghcr.io/sellerai-com/sellerclaw-agent:latest")


@pytest.mark.parametrize(
    ("args", "expected_api", "expected_web"),
    [
        pytest.param([], "https://api.sellerclaw.ai", "https://app.sellerclaw.ai", id="production-default"),
        pytest.param(
            ["--env", "staging"],
            "https://sellerclaw-staging.fly.dev",
            "https://staging-app.sellerclaw.ai",
            id="staging",
        ),
        pytest.param(
            ["--env=local"],
            "http://host.docker.internal:8000",
            "http://localhost:5173",
            id="local",
        ),
    ],
)
def test_profile_selects_cloud(
    sandbox: Sandbox,
    args: list[str],
    expected_api: str,
    expected_web: str,
) -> None:
    result = _run(sandbox, ["--dry-run", *args])
    run_line = _docker_run_line(sandbox, result)

    assert f"-e SELLERCLAW_API_URL={expected_api}" in run_line
    assert f"-e SELLERCLAW_WEB_URL={expected_web}" in run_line


@pytest.mark.parametrize(
    ("args", "expected_tag"),
    [
        pytest.param([], "latest", id="default-latest"),
        pytest.param(["--version", "0.56.0"], "0.56.0", id="pinned-version"),
        pytest.param(["--version", "v0.56.0"], "0.56.0", id="pinned-version-with-v-prefix"),
        pytest.param(["--version=0.55.1"], "0.55.1", id="pinned-version-equals-form"),
        pytest.param(["--beta"], "beta", id="prerelease-channel"),
    ],
)
def test_version_selection(sandbox: Sandbox, args: list[str], expected_tag: str) -> None:
    result = _run(sandbox, ["--dry-run", *args])

    assert f"+ docker pull ghcr.io/sellerai-com/sellerclaw-agent:{expected_tag}" in result.stdout


# ---------------------------------------------------------------------------
# Rejected input
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("args", "expected_error"),
    [
        pytest.param(["--env", "prod"], "Invalid --env 'prod'", id="unknown-profile"),
        pytest.param(["--version"], "--version requires a value", id="version-without-value"),
        pytest.param(["--frobnicate"], "Unknown option: --frobnicate", id="unknown-option"),
    ],
)
def test_bad_arguments_fail_with_a_readable_message(
    sandbox: Sandbox,
    args: list[str],
    expected_error: str,
) -> None:
    result = _run(sandbox, ["--dry-run", *args])

    assert result.returncode != 0
    assert expected_error in result.stderr


def test_too_little_ram_stops_before_downloading(sandbox: Sandbox) -> None:
    (sandbox.work / "meminfo").write_text(f"MemTotal: {1024 * 1024} kB\n")

    result = _run(sandbox, ["--dry-run"])

    assert result.returncode != 0
    assert "Not enough RAM" in result.stderr
    assert "docker pull" not in result.stdout


def test_unsupported_architecture_is_refused(sandbox: Sandbox) -> None:
    _write_stub(sandbox.bin, "uname", 'case "${1:-}" in -m) echo riscv64 ;; *) echo Linux ;; esac')

    result = _run(sandbox, ["--dry-run"])

    assert result.returncode != 0
    assert "Unsupported CPU architecture" in result.stderr


def test_arm_machine_is_accepted(sandbox: Sandbox) -> None:
    _write_stub(sandbox.bin, "uname", 'case "${1:-}" in -m) echo arm64 ;; *) echo Darwin ;; esac')
    _write_stub(sandbox.bin, "sysctl", 'echo "$((8 * 1024 * 1024 * 1024))"')

    result = _run(sandbox, ["--dry-run"])

    assert result.returncode == 0, result.stderr
    assert "platform: Darwin arm64" in result.stdout


def test_missing_docker_without_a_terminal_asks_for_yes(sandbox: Sandbox) -> None:
    """No docker, no tty: installing Docker is privileged, so consent must be explicit."""
    result = _run(sandbox, [])

    assert result.returncode != 0
    assert "--yes" in result.stderr


# ---------------------------------------------------------------------------
# A full (stubbed) install
# ---------------------------------------------------------------------------

def test_install_pulls_starts_and_signs_in(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr + result.stdout
    calls = sandbox.docker_calls()
    assert any(c.startswith("pull ghcr.io/sellerai-com/sellerclaw-agent:latest") for c in calls)
    assert any(c.startswith("run -d --name sellerclaw-agent") for c in calls)
    # Health check, then the browser sign-in — both inside the container, no host Python.
    assert any("curl -fsS http://127.0.0.1:8001/health" in c for c in calls)
    assert any("python -m sellerclaw_agent login --browser" in c for c in calls)


def test_install_writes_the_control_command(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr
    wrapper = sandbox.wrapper
    assert wrapper.is_file()
    assert wrapper.stat().st_mode & 0o111
    body = wrapper.read_text()
    assert "CONTAINER_NAME=sellerclaw-agent" in body
    for command in ("status)", "login)", "stop)", "update)", "uninstall)"):
        assert command in body


def test_reinstall_replaces_container_but_keeps_the_data_volume(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent", connected=True)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr
    calls = sandbox.docker_calls()
    assert any(c.startswith("rm -f sellerclaw-agent") for c in calls)
    assert not any(c.startswith("volume rm") for c in calls)
    # Already signed in — an upgrade must not send the user through sign-in again.
    assert not any("login --browser" in c for c in calls)
    assert "already signed in" in result.stdout


def test_old_image_says_so_instead_of_failing_at_sign_in(sandbox: Sandbox) -> None:
    """`--version <old>` pulls an image with no in-container CLI: say it in one sentence."""
    _install_docker_stub(sandbox, old_image=True)

    result = _run(sandbox, ["--yes", "--version", "0.40.0"])

    assert result.returncode == 0, result.stderr
    assert "too old to sign in from here" in result.stdout
    assert not any("login --browser" in c for c in sandbox.docker_calls())


def test_no_login_flag_only_starts_the_agent(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox)

    result = _run(sandbox, ["--yes", "--no-login"])

    assert result.returncode == 0, result.stderr
    assert not any("login --browser" in c for c in sandbox.docker_calls())


def test_uninstall_removes_container_and_command_but_keeps_data(sandbox: Sandbox) -> None:
    """Losing the sign-in must never be a side effect of a scripted uninstall."""
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent")
    assert _run(sandbox, ["--yes"]).returncode == 0
    assert sandbox.wrapper.is_file()

    result = _run(sandbox, ["--uninstall", "--yes"])

    assert result.returncode == 0, result.stderr
    assert not sandbox.wrapper.exists()
    assert any(c.startswith("rm -f sellerclaw-agent") for c in sandbox.docker_calls())
    assert not any(c.startswith("volume rm") for c in sandbox.docker_calls())
    assert "docker volume rm sellerclaw-agent-data" in result.stdout


def test_uninstall_purge_deletes_the_data_volume(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent")

    result = _run(sandbox, ["--uninstall", "--purge", "--yes"])

    assert result.returncode == 0, result.stderr
    assert any(c.startswith("volume rm sellerclaw-agent-data") for c in sandbox.docker_calls())
