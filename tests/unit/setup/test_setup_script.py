from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SETUP_SH = REPO_ROOT / "setup.sh"

# Tools the script and its heredocs need. Everything else (docker, uv, sudo,
# systemctl, ...) stays out unless a test explicitly stubs it, so "missing
# dependency" cases are not masked by the host's real binaries.
SYSBIN_TOOLS = (
    "bash",
    "cat",
    "chmod",
    "dirname",
    "env",
    "grep",
    "head",
    "ls",
    "mkdir",
    "mktemp",
    "printf",
    "pwd",
    "rm",
    "sed",
    "sh",
    "sleep",
    "tee",
    "true",
    "false",
    # `uname` runs unconditionally in setup.sh. Tests that care about the OS
    # override it with a stub in bin/; otherwise the real Linux uname is fine.
    "uname",
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
    # Absolute shebang: the restricted PATH excludes /bin and /usr/bin, so
    # `/usr/bin/env bash` would fail to resolve.
    path = bin_dir / name
    path.write_text(f"#!/bin/bash\nset -e\n{body}\n")
    path.chmod(0o755)


@dataclass
class Sandbox:
    work: Path
    bin: Path
    sysbin: Path
    home: Path

    @property
    def path(self) -> str:
        return f"{self.bin}:{self.sysbin}"

    @property
    def env(self) -> dict[str, str]:
        return {
            "PATH": self.path,
            "HOME": str(self.home),
            "LANG": "C",
            "LC_ALL": "C",
        }


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(SETUP_SH, work / "setup.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sysbin = tmp_path / "sysbin"
    _populate_sysbin(sysbin)
    home = tmp_path / "home"
    home.mkdir()
    return Sandbox(work=work, bin=bin_dir, sysbin=sysbin, home=home)


def _run(
    sandbox: Sandbox,
    args: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
    stdin_input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**sandbox.env, **(env_overrides or {})}
    return subprocess.run(
        ["/bin/bash", str(sandbox.work / "setup.sh"), *args],
        cwd=sandbox.work,
        env=env,
        input=stdin_input if stdin_input is not None else "",
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_env_file(sandbox: Sandbox, profile: str, body: str = "") -> None:
    (sandbox.work / f".env.{profile}").write_text(body)


def _install_uname(sandbox: Sandbox, os_name: str = "Linux") -> None:
    _write_stub(sandbox.bin, "uname", f'echo "{os_name}"')


def _install_awk(sandbox: Sandbox, ram_mb: int) -> None:
    ram_kb = ram_mb * 1024
    _write_stub(sandbox.bin, "awk", f'echo "{ram_kb}"')


def _install_docker(
    sandbox: Sandbox,
    *,
    compose_ok: bool = True,
    info_ok: bool = True,
) -> None:
    compose_branch = (
        "echo 'Docker Compose version v2.20.0'" if compose_ok else "exit 1"
    )
    info_branch = "echo 'Server: docker'" if info_ok else "exit 1"
    body = textwrap.dedent(f"""
        case "${{1:-}}" in
            --version) echo "Docker version 24.0.0" ;;
            compose)
                case "${{2:-}}" in
                    version) {compose_branch} ;;
                    *) exit 0 ;;
                esac
                ;;
            info) {info_branch} ;;
            *) ;;
        esac
    """).strip()
    _write_stub(sandbox.bin, "docker", body)


def _install_uv(
    sandbox: Sandbox,
    *,
    exit_code: int = 0,
    capture_file: Path | None = None,
    extra_body: str = "",
) -> None:
    capture = (
        f'printf \'%s\\n\' "$@" > "{capture_file}"' if capture_file else ""
    )
    body = textwrap.dedent(f"""
        if [[ "${{1:-}}" == "--version" ]]; then
            echo "uv 0.5.0"
            exit 0
        fi
        {capture}
        {extra_body}
        exit {exit_code}
    """).strip()
    _write_stub(sandbox.bin, "uv", body)


def _install_full_baseline(sandbox: Sandbox) -> None:
    """All host prereqs present; script should reach the CLI handoff."""
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=8192)
    _install_docker(sandbox)
    _install_uv(sandbox)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_help_prints_usage_and_exits_zero(sandbox: Sandbox) -> None:
    result = _run(sandbox, ["--help"])

    assert result.returncode == 0, result.stderr
    # The help text must describe the user-visible surface area so users can
    # discover the key subcommands without guessing.
    expected_fragments = [
        "Usage: ./setup.sh",
        "Commands:",
        "setup",
        "start",
        "stop",
        "status",
        "login",
        "logout",
        "--env",
        "--yes",
    ]
    for fragment in expected_fragments:
        assert fragment in result.stdout, (
            f"help missing fragment {fragment!r}; got:\n{result.stdout}"
        )


def test_env_flag_requires_value(sandbox: Sandbox) -> None:
    result = _run(sandbox, ["--env"])

    assert result.returncode != 0
    assert "--env requires a value" in result.stderr
    assert "local, staging, production" in result.stderr


@pytest.mark.parametrize(
    "bad_env",
    [
        pytest.param("foo", id="garbage"),
        pytest.param("dev", id="not-in-allowed-set"),
        pytest.param("", id="empty-string"),
        pytest.param("PRODUCTION", id="wrong-case"),
    ],
)
def test_env_flag_rejects_invalid_profile(
    sandbox: Sandbox, bad_env: str
) -> None:
    result = _run(sandbox, ["--env", bad_env])

    assert result.returncode != 0
    assert f"Invalid --env '{bad_env}'" in result.stderr
    assert "local, staging, production" in result.stderr


@pytest.mark.parametrize(
    "profile",
    [
        pytest.param("local", id="local"),
        pytest.param("staging", id="staging"),
        pytest.param("production", id="production"),
    ],
)
def test_env_flag_accepts_known_profiles(
    sandbox: Sandbox, profile: str
) -> None:
    _install_full_baseline(sandbox)
    _write_env_file(sandbox, profile)

    result = _run(sandbox, ["--env", profile])

    assert result.returncode == 0, result.stderr


def test_env_flag_equals_form_is_supported(sandbox: Sandbox) -> None:
    _install_full_baseline(sandbox)
    _write_env_file(sandbox, "local", body='MARKER="from-local"\n')
    probe = sandbox.work / "env_probe.txt"
    _install_uv(
        sandbox,
        extra_body=f'echo "AGENT_ENV=${{AGENT_ENV:-}}|MARKER=${{MARKER:-}}" > "{probe}"',
    )

    result = _run(sandbox, ["--env=local"])

    assert result.returncode == 0, result.stderr
    assert probe.read_text().strip() == "AGENT_ENV=local|MARKER=from-local"


# ---------------------------------------------------------------------------
# Env file requirement depends on the subcommand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subcmd",
    [
        pytest.param([], id="default-setup"),
        pytest.param(["setup"], id="setup"),
        pytest.param(["start"], id="start"),
        pytest.param(["stop"], id="stop"),
    ],
)
def test_missing_env_file_fails_for_subcommands_that_need_it(
    sandbox: Sandbox, subcmd: list[str]
) -> None:
    # Provide all binaries so we're NOT bailing on something else first.
    _install_full_baseline(sandbox)
    # No .env.production on disk.

    result = _run(sandbox, subcmd)

    assert result.returncode != 0
    assert "Environment file '.env.production' not found" in result.stderr


@pytest.mark.parametrize(
    "subcmd",
    [
        pytest.param(["status"], id="status"),
        pytest.param(["login"], id="login"),
        pytest.param(["logout"], id="logout"),
    ],
)
def test_subcommands_that_do_not_need_env_file_succeed_without_one(
    sandbox: Sandbox, subcmd: list[str]
) -> None:
    # Only uv is required for these; no env file, no docker.
    _install_uv(sandbox)

    result = _run(sandbox, subcmd)

    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# OS / hardware preflight (installer path only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "os_name",
    [
        pytest.param("Darwin", id="macos"),
        pytest.param("FreeBSD", id="freebsd"),
        pytest.param("MINGW64_NT", id="windows-git-bash"),
    ],
)
def test_installer_rejects_non_linux_os(
    sandbox: Sandbox, os_name: str
) -> None:
    _write_env_file(sandbox, "production")
    _install_uname(sandbox, os_name=os_name)

    result = _run(sandbox, [])

    assert result.returncode != 0
    assert "This installer supports Linux only" in result.stderr
    assert os_name in result.stderr


def test_non_installer_subcommand_does_not_enforce_linux(
    sandbox: Sandbox,
) -> None:
    """`status` does not run the installer, so OS is not gated."""
    _install_uname(sandbox, os_name="Darwin")
    _install_uv(sandbox)

    result = _run(sandbox, ["status"])

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "ram_mb",
    [
        pytest.param(0, id="zero"),
        pytest.param(512, id="512mb"),
        pytest.param(2048, id="exactly-threshold-is-rejected"),
    ],
)
def test_installer_rejects_insufficient_ram(
    sandbox: Sandbox, ram_mb: int
) -> None:
    _write_env_file(sandbox, "production")
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=ram_mb)

    result = _run(sandbox, [])

    assert result.returncode != 0
    assert "Not enough RAM" in result.stderr
    assert f"detected {ram_mb} MB" in result.stderr
    assert "requires more than 2048 MB" in result.stderr


def test_installer_accepts_ram_just_above_threshold(
    sandbox: Sandbox,
) -> None:
    _write_env_file(sandbox, "production")
    _install_uname(sandbox)
    # MEM_MB must be strictly > 2048; 2049 is the smallest accepted value.
    _install_awk(sandbox, ram_mb=2049)
    _install_docker(sandbox)
    _install_uv(sandbox)

    result = _run(sandbox, [])

    assert result.returncode == 0, result.stderr
    assert "RAM: 2049 MB" in result.stdout


# ---------------------------------------------------------------------------
# Dependency checks — installer path (interactive prompt, no --yes)
# ---------------------------------------------------------------------------


def test_installer_missing_docker_without_yes_fails_with_hint(
    sandbox: Sandbox,
) -> None:
    _write_env_file(sandbox, "production")
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=8192)
    # No docker stub -> have_cmd docker == false.

    result = _run(sandbox, [])

    assert result.returncode != 0
    assert "Missing dependency: Docker Engine" in result.stderr
    assert "--yes" in result.stderr


def test_installer_missing_docker_compose_plugin_without_yes(
    sandbox: Sandbox,
) -> None:
    _write_env_file(sandbox, "production")
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=8192)
    _install_docker(sandbox, compose_ok=False)

    result = _run(sandbox, [])

    assert result.returncode != 0
    assert "Missing dependency: Docker Compose v2 plugin" in result.stderr


def test_installer_docker_daemon_unreachable_hints_systemctl(
    sandbox: Sandbox,
) -> None:
    _write_env_file(sandbox, "production")
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=8192)
    _install_docker(sandbox, info_ok=False)
    # No systemctl stub -> script skips auto-start and reports the error.

    result = _run(sandbox, [])

    assert result.returncode != 0
    assert "Docker daemon is not reachable" in result.stderr
    assert "sudo systemctl start docker" in result.stderr


def test_installer_missing_uv_without_yes_fails(sandbox: Sandbox) -> None:
    _write_env_file(sandbox, "production")
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=8192)
    _install_docker(sandbox)
    # No uv stub.

    result = _run(sandbox, [])

    assert result.returncode != 0
    assert "Missing dependency: uv" in result.stderr


# ---------------------------------------------------------------------------
# Dependency checks — non-installer subcommands point user back to ./setup.sh
# ---------------------------------------------------------------------------


def test_start_without_docker_installed_directs_user_to_setup(
    sandbox: Sandbox,
) -> None:
    """For non-installer subcommands, the script must not try to install."""
    _write_env_file(sandbox, "production")
    _install_uv(sandbox)
    # No docker on PATH.

    result = _run(sandbox, ["start"])

    assert result.returncode != 0
    assert "Docker is not installed" in result.stderr
    assert "Run ./setup.sh first" in result.stderr


def test_start_without_docker_compose_plugin_directs_user_to_setup(
    sandbox: Sandbox,
) -> None:
    _write_env_file(sandbox, "production")
    _install_uv(sandbox)
    _install_docker(sandbox, compose_ok=False)

    result = _run(sandbox, ["start"])

    assert result.returncode != 0
    assert "Docker Compose v2 is not installed" in result.stderr
    assert "Run ./setup.sh first" in result.stderr


def test_stop_without_docker_daemon_running_fails(sandbox: Sandbox) -> None:
    _write_env_file(sandbox, "production")
    _install_uv(sandbox)
    _install_docker(sandbox, info_ok=False)

    result = _run(sandbox, ["stop"])

    assert result.returncode != 0
    assert "Docker daemon is not reachable" in result.stderr


def test_status_without_uv_directs_user_to_setup(sandbox: Sandbox) -> None:
    # No uv on PATH; status doesn't need docker/env, so uv is the only gate.
    result = _run(sandbox, ["status"])

    assert result.returncode != 0
    assert "uv is not installed" in result.stderr
    assert "Run ./setup.sh first" in result.stderr


# ---------------------------------------------------------------------------
# Happy path: handoff to the CLI
# ---------------------------------------------------------------------------


def test_installer_happy_path_execs_cli_with_default_setup_command(
    sandbox: Sandbox,
) -> None:
    _write_env_file(sandbox, "production")
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=8192)
    _install_docker(sandbox)
    capture = sandbox.work / "uv_args.txt"
    _install_uv(sandbox, capture_file=capture)

    result = _run(sandbox, [])

    assert result.returncode == 0, result.stderr
    for header in ("Checking hardware", "Checking Docker", "Checking uv"):
        assert header in result.stdout, (
            f"missing header {header!r}; got:\n{result.stdout}"
        )
    forwarded = capture.read_text().splitlines()
    assert forwarded == ["run", "--quiet", "sellerclaw-agent", "setup"]


@pytest.mark.parametrize(
    ("cli_args", "expected_subcmd"),
    [
        pytest.param(["status"], "status", id="status"),
        pytest.param(["login"], "login", id="login"),
        pytest.param(["logout"], "logout", id="logout"),
        pytest.param(["help"], "help", id="help-subcommand"),
    ],
)
def test_non_installer_subcommands_forward_to_cli(
    sandbox: Sandbox, cli_args: list[str], expected_subcmd: str
) -> None:
    capture = sandbox.work / "uv_args.txt"
    _install_uv(sandbox, capture_file=capture)

    result = _run(sandbox, cli_args)

    assert result.returncode == 0, result.stderr
    forwarded = capture.read_text().splitlines()
    assert forwarded == ["run", "--quiet", "sellerclaw-agent", expected_subcmd]


def test_unknown_subcommand_is_forwarded_to_cli(sandbox: Sandbox) -> None:
    """Unknown subcommands must be handed off (CLI shows its own error)."""
    capture = sandbox.work / "uv_args.txt"
    _install_uv(sandbox, capture_file=capture)

    result = _run(sandbox, ["wibble", "--flag"])

    assert result.returncode == 0, result.stderr
    forwarded = capture.read_text().splitlines()
    assert forwarded == [
        "run",
        "--quiet",
        "sellerclaw-agent",
        "wibble",
        "--flag",
    ]


def test_env_file_values_are_exported_to_cli(sandbox: Sandbox) -> None:
    _write_env_file(
        sandbox,
        "staging",
        body='CUSTOM_FROM_ENV_FILE="yep"\n',
    )
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=8192)
    _install_docker(sandbox)
    probe = sandbox.work / "uv_env_probe.txt"
    _install_uv(
        sandbox,
        extra_body=(
            '{ '
            f'echo "AGENT_ENV=${{AGENT_ENV:-<unset>}}"; '
            f'echo "CUSTOM_FROM_ENV_FILE=${{CUSTOM_FROM_ENV_FILE:-<unset>}}"; '
            f'}} > "{probe}"'
        ),
    )

    result = _run(sandbox, ["--env", "staging"])

    assert result.returncode == 0, result.stderr
    captured = probe.read_text()
    assert "AGENT_ENV=staging" in captured
    assert "CUSTOM_FROM_ENV_FILE=yep" in captured


def test_cli_failure_propagates_exit_code(sandbox: Sandbox) -> None:
    """Covers downstream failures: bad creds, server-start error, etc.

    setup.sh exec's uv; whatever exit code the CLI returns becomes the
    script's exit code. This is the only channel by which credential and
    server-launch errors surface through the wrapper.
    """
    _write_env_file(sandbox, "production")
    _install_uname(sandbox)
    _install_awk(sandbox, ram_mb=8192)
    _install_docker(sandbox)
    _install_uv(sandbox, exit_code=7)

    result = _run(sandbox, [])

    assert result.returncode == 7
