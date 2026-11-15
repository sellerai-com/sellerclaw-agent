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
    login_exit: int = 0,
    where_exit: int = 0,
    supports_where: bool = True,
) -> None:
    """Stub `docker` that records every call and answers the handful of queries used.

    ``login_exit`` / ``where_exit``: what the in-container `login` / `where` answer — 0 the agent
    runs here, 3 it keeps running in SellerClaw's cloud, anything else a failure.
    """
    status_line = "Connected as Tester" if connected else "Not connected"
    entry_point_branch = "exit 1" if old_image else "exit 0"
    where_branch = "exit 0" if supports_where and not old_image else "exit 1"
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
                if [[ "$*" == *"import cmd_where"* ]]; then {where_branch}; fi
                if [[ "$*" == *"sellerclaw_agent status"* ]]; then echo "{status_line}"; fi
                if [[ "$*" == *"sellerclaw_agent login"* ]]; then exit {login_exit}; fi
                if [[ "$*" == *"sellerclaw_agent where"* ]]; then exit {where_exit}; fi
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


def test_reinstall_lets_the_old_agent_say_goodbye_before_removing_it(sandbox: Sandbox) -> None:
    """A graceful stop tells the cloud the agent went offline: the new container is let in at once."""
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent", connected=True)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr
    calls = sandbox.docker_calls()
    stop_at = calls.index("stop -t 30 sellerclaw-agent")
    remove_at = calls.index("rm -f sellerclaw-agent")
    run_at = next(i for i, c in enumerate(calls) if c.startswith("run -d --name sellerclaw-agent"))
    assert stop_at < remove_at < run_at


@pytest.mark.parametrize(
    ("login_exit", "expected", "unexpected"),
    [
        pytest.param(0, "Your agent runs on this computer.", "still runs in SellerClaw's cloud", id="runs-here"),
        pytest.param(
            3,
            'Your agent still runs in SellerClaw\'s cloud.',
            "Your agent runs on this computer",
            id="stays-in-the-cloud",
        ),
        pytest.param(
            4,
            "Your agent is still moving to this computer.",
            "Connecting did not finish",
            id="hand-over-still-going",
        ),
        pytest.param(1, "Connecting did not finish", "Your agent runs on this computer", id="sign-in-failed"),
    ],
)
def test_final_message_says_where_the_agent_runs(
    sandbox: Sandbox, login_exit: int, expected: str, unexpected: str
) -> None:
    _install_docker_stub(sandbox, login_exit=login_exit)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert unexpected not in result.stdout
    # The owner is never sent to the web panel to flip a switch the installer already handled.
    assert "switch to self-hosted" not in result.stdout


def test_an_agent_left_in_the_cloud_is_told_how_to_move_it_later(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox, login_exit=3)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr
    assert 'open Hosting in SellerClaw and press "Move to your computer"' in result.stdout
    # A plan only powers an agent that runs on this computer: not offered while it stays in the cloud.
    assert "sellerclaw-agent connect chatgpt" not in result.stdout


@pytest.mark.parametrize(
    ("where_exit", "expected"),
    [
        pytest.param(0, "Your agent runs on this computer.", id="runs-here"),
        pytest.param(3, "Your agent still runs in SellerClaw's cloud.", id="stays-in-the-cloud"),
        pytest.param(4, "Your agent is still moving to this computer.", id="hand-over-still-going"),
        pytest.param(1, "SellerClaw Agent is installed and running.", id="unknown"),
    ],
)
def test_an_already_signed_in_agent_is_asked_where_it_runs(
    sandbox: Sandbox, where_exit: int, expected: str
) -> None:
    """Re-running the installer on a signed-in computer: no new sign-in, but the place still counts."""
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent", connected=True, where_exit=where_exit)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr
    calls = sandbox.docker_calls()
    assert any(c.endswith("python -m sellerclaw_agent where") for c in calls)
    assert not any("login --browser" in c for c in calls)
    assert expected in result.stdout


def test_an_image_without_where_keeps_the_plain_final_message(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent", connected=True, supports_where=False)

    result = _run(sandbox, ["--yes", "--version", "0.60.0"])

    assert result.returncode == 0, result.stderr
    assert not any(c.endswith("python -m sellerclaw_agent where") for c in sandbox.docker_calls())
    assert "SellerClaw Agent is installed and running." in result.stdout


def test_sign_in_and_the_move_question_are_asked_on_the_terminal() -> None:
    """`curl | sh` hands the script its own bytes on stdin; the question about moving the agent from
    SellerClaw's cloud must reach the terminal — like the AI-plan question does. (The unattended
    runs above cannot open /dev/tty, so this one reads the script.)"""
    script = INSTALL_SH.read_text()

    assert 'python -m sellerclaw_agent "$@" </dev/tty' in script
    assert "agent_cli_in_container login --browser" in script
    assert "agent_cli_in_container where" in script


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
    """Deleting stored data must never be a side effect of a scripted uninstall."""
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent")
    assert _run(sandbox, ["--yes"]).returncode == 0
    assert sandbox.wrapper.is_file()

    result = _run(sandbox, ["--uninstall", "--yes"])

    assert result.returncode == 0, result.stderr
    assert not sandbox.wrapper.exists()
    assert any(c.startswith("rm -f sellerclaw-agent") for c in sandbox.docker_calls())
    assert not any(c.startswith("volume rm") for c in sandbox.docker_calls())
    assert "docker volume rm sellerclaw-agent-data" in result.stdout
    assert 'press "Move to the cloud"' in result.stdout


@pytest.mark.parametrize(
    ("connected", "signs_out"),
    [
        pytest.param(True, True, id="signed-in-signs-out-first"),
        pytest.param(False, False, id="not-signed-in-nothing-to-revoke"),
    ],
)
def test_uninstall_signs_out_then_stops_gracefully(sandbox: Sandbox, connected: bool, signs_out: bool) -> None:
    """The cloud revokes this computer's token before the agent goes; then it says goodbye."""
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent", connected=connected)

    result = _run(sandbox, ["--uninstall", "--yes"])

    assert result.returncode == 0, result.stderr
    calls = sandbox.docker_calls()
    logout = "exec -w /app sellerclaw-agent python -m sellerclaw_agent logout"
    stop_at = calls.index("stop -t 30 sellerclaw-agent")
    remove_at = calls.index("rm -f sellerclaw-agent")
    assert stop_at < remove_at
    if signs_out:
        assert calls.index(logout) < stop_at
        assert "signed out of your SellerClaw account" in result.stdout
    else:
        assert logout not in calls


def test_uninstall_purge_deletes_the_data_volume(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox, existing_container="sellerclaw-agent")

    result = _run(sandbox, ["--uninstall", "--purge", "--yes"])

    assert result.returncode == 0, result.stderr
    assert any(c.startswith("volume rm sellerclaw-agent-data") for c in sandbox.docker_calls())


def test_control_command_connects_and_disconnects_the_owners_plan_as_node(sandbox: Sandbox) -> None:
    _install_docker_stub(sandbox)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr
    body = sandbox.wrapper.read_text()
    assert "connect)   shift; agent_cli_node connect" in body
    assert "disconnect) agent_cli_node disconnect" in body
    # What `connect` installs and signs in must belong to the user OpenClaw runs as.
    assert "-u node -e HOME=/home/node" in body
    assert "connect     Choose what powers the agent: SellerClaw credits or your own plan" in body
    assert "connect claude  Run the agent on your own Claude plan" in body
    assert "connect chatgpt Run the agent on your own ChatGPT plan" in body


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(["status"], id="status"),
        pytest.param(["connect", "claude"], id="connect-as-node"),
    ],
)
def test_control_command_prints_times_on_this_computers_clock(sandbox: Sandbox, command: list[str]) -> None:
    """When a plan's limit resets reads on the owner's clock, not the container's UTC."""
    _install_docker_stub(sandbox)
    assert _run(sandbox, ["--yes"]).returncode == 0
    calls_before = len(sandbox.docker_calls())

    result = subprocess.run(
        ["/bin/sh", str(sandbox.wrapper), *command],
        env={**sandbox.env, "TZ": "Europe/Moscow"},
        input="",
        capture_output=True,
        text=True,
        timeout=30,
        start_new_session=True,
    )

    assert result.returncode == 0, result.stderr
    [call] = sandbox.docker_calls()[calls_before:]
    assert call.startswith("exec -i -e TZ=Europe/Moscow ")
    assert call.endswith("python -m sellerclaw_agent " + " ".join(command))


def test_an_unattended_install_never_asks_about_the_ai_plan(sandbox: Sandbox) -> None:
    """`--yes` / no terminal: credits by default, and no one to sign in on Anthropic's page."""
    _install_docker_stub(sandbox)

    result = _run(sandbox, ["--yes"])

    assert result.returncode == 0, result.stderr
    calls = sandbox.docker_calls()
    assert not any("connect --supported" in c for c in calls)
    assert not any("sellerclaw_agent connect claude" in c for c in calls)
    assert not any("sellerclaw_agent connect chatgpt" in c for c in calls)
    assert not any(c.endswith("python -m sellerclaw_agent connect") for c in calls)  # the question itself
    # The final message says how, for both plans.
    assert "sellerclaw-agent connect chatgpt | claude" in result.stdout
