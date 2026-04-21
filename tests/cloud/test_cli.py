from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from rich.console import Console
from sellerclaw_agent.cli import (
    _clear_local_control_plane_key_cache,
    _compose_env_file_args,
    _compose_profile_env_file,
    _connect_password,
    _device_flow,
    _diagnose_compose_failure,
    _ensure_local_api_key,
    _local_control_plane_auth_headers,
    _require_agent_env,
    _wait_for_cloud_live,
    agent_base_url,
    agent_root,
    main,
    parse_command,
    wait_for_agent,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _cli_local_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "cli-test-local-key")
    _clear_local_control_plane_key_cache()


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param([], "setup", id="default-setup"),
        pytest.param(["login"], "login", id="login"),
        pytest.param(["help"], "help", id="help"),
        pytest.param(["--help"], "help", id="dash-help"),
    ],
)
def test_parse_command(argv: list[str], expected: str) -> None:
    assert parse_command(argv) == expected


def test_agent_base_url_returns_localhost_8001() -> None:
    assert agent_base_url() == "http://127.0.0.1:8001"


def test_agent_root_contains_sellerclaw_agent_package() -> None:
    assert (agent_root() / "sellerclaw_agent").is_dir()


def test_wait_for_agent_success() -> None:
    mock_cm = MagicMock()
    mock_cm.get.return_value = MagicMock(status_code=200)
    mock_cm.__enter__.return_value = mock_cm
    mock_cm.__exit__.return_value = None

    console = Console(record=True)
    with patch("sellerclaw_agent.cli.httpx.Client", return_value=mock_cm):
        ok = wait_for_agent("http://127.0.0.1:8001", console, timeout_s=2.0)
    assert ok is True


def test_main_unknown_command_exits_2() -> None:
    with patch.object(sys, "argv", ["sellerclaw-agent", "nope"]):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2


def test_main_help_exits_0() -> None:
    with patch.object(sys, "argv", ["sellerclaw-agent", "help"]):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0


def test_main_status_unreachable() -> None:
    with patch.object(sys, "argv", ["sellerclaw-agent", "status"]):
        with patch("sellerclaw_agent.cli.get_auth_status", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(SystemExit) as excinfo:
                main()
            assert excinfo.value.code == 1


def test_connect_password_401_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_r = MagicMock()
    mock_r.status_code = 401
    mock_r.json.return_value = {"detail": "Invalid credentials"}
    mock_r.headers.get.return_value = "application/json"
    mock_cm = MagicMock()
    mock_cm.post.return_value = mock_r
    mock_cm.__enter__.return_value = mock_cm
    mock_cm.__exit__.return_value = None
    monkeypatch.setattr("sellerclaw_agent.cli.httpx.Client", MagicMock(return_value=mock_cm))
    console = Console(record=True)
    assert _connect_password(console, "http://127.0.0.1:8001", "a@b.c", "secret") is False


def test_connect_password_success(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_r = MagicMock()
    mock_r.status_code = 200
    mock_r.raise_for_status = MagicMock()
    mock_cm = MagicMock()
    mock_cm.post.return_value = mock_r
    mock_cm.__enter__.return_value = mock_cm
    mock_cm.__exit__.return_value = None
    monkeypatch.setattr("sellerclaw_agent.cli.httpx.Client", MagicMock(return_value=mock_cm))
    assert (
        _connect_password(Console(record=True), "http://127.0.0.1:8001", "a@b.c", "secret")
        is True
    )
    mock_r.raise_for_status.assert_called_once()


def test_require_agent_env_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_ENV", raising=False)
    with pytest.raises(RuntimeError, match="AGENT_ENV is not set"):
        _require_agent_env()


def test_require_agent_env_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ENV", "weird")
    with pytest.raises(RuntimeError, match="Invalid AGENT_ENV"):
        _require_agent_env()


def test_require_agent_env_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ENV", "production")
    assert _require_agent_env() == "production"


def test_compose_profile_env_file_requires_env_and_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AGENT_ENV", raising=False)
    with pytest.raises(RuntimeError, match="AGENT_ENV is not set"):
        _compose_profile_env_file(tmp_path)

    monkeypatch.setenv("AGENT_ENV", "staging")
    with pytest.raises(RuntimeError, match="Environment file not found"):
        _compose_profile_env_file(tmp_path)

    (tmp_path / ".env.staging").write_text("FOO=bar\n", encoding="utf-8")
    assert _compose_profile_env_file(tmp_path) == tmp_path / ".env.staging"


def test_compose_env_file_args_uses_only_profile_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENT_ENV", "local")
    (tmp_path / ".env.local").write_text("A=1\n", encoding="utf-8")
    (tmp_path / "secrets.env").write_text("B=2\n", encoding="utf-8")
    assert _compose_env_file_args(tmp_path) == ["--env-file", str(tmp_path / ".env.local")]


def test_ensure_local_api_key_prefers_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "from-env")
    assert _ensure_local_api_key(tmp_path) == "from-env"
    assert not (tmp_path / "data" / "local_api_key").exists()


def test_ensure_local_api_key_reads_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "local_api_key").write_text("saved-value\n", encoding="utf-8")
    assert _ensure_local_api_key(tmp_path) == "saved-value"


def test_ensure_local_api_key_generates_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    key = _ensure_local_api_key(tmp_path)
    assert key
    path = tmp_path / "data" / "local_api_key"
    assert path.is_file()
    assert path.read_text(encoding="utf-8").strip() == key
    # Second call is stable.
    assert _ensure_local_api_key(tmp_path) == key


def test_local_control_plane_auth_headers_uses_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    _clear_local_control_plane_key_cache()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "local_api_key").write_text("file-bearer\n", encoding="utf-8")
    monkeypatch.setattr("sellerclaw_agent.cli.agent_root", lambda: tmp_path)
    headers = _local_control_plane_auth_headers("http://127.0.0.1:8001")
    assert headers == {"Authorization": "Bearer file-bearer"}


def test_diagnose_compose_failure_network_timeout() -> None:
    raw = (
        "failed to solve: ghcr.io/openclaw/openclaw:2026.4.15: failed to resolve source "
        'metadata for ghcr.io/openclaw/openclaw:2026.4.15: failed to do request: Head '
        '"https://ghcr.io/v2/openclaw/openclaw/manifests/2026.4.15": '
        "dial tcp 140.82.121.34:443: i/o timeout"
    )
    title, hints = _diagnose_compose_failure(raw)
    assert "download the Docker base image" in title.lower() or "image" in title.lower()
    assert hints
    assert any("internet" in h.lower() or "ghcr" in h.lower() for h in hints)


def test_diagnose_compose_failure_daemon_permission() -> None:
    raw = "permission denied while trying to connect to the Docker daemon socket at /var/run/docker.sock"
    title, hints = _diagnose_compose_failure(raw)
    assert "docker" in title.lower()
    assert any("docker" in h.lower() and "group" in h.lower() for h in hints)


def test_diagnose_compose_failure_daemon_down() -> None:
    raw = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
    title, hints = _diagnose_compose_failure(raw)
    assert "daemon" in title.lower()
    assert any("systemctl" in h.lower() for h in hints)


def test_diagnose_compose_failure_port_in_use() -> None:
    raw = 'Error starting userland proxy: listen tcp 0.0.0.0:8001: bind: address already in use'
    title, hints = _diagnose_compose_failure(raw)
    assert "port" in title.lower()
    assert hints


def test_diagnose_compose_failure_unknown_falls_through() -> None:
    title, hints = _diagnose_compose_failure("something exploded for no reason")
    assert "Docker Compose failed" in title
    assert hints == []


def test_device_flow_completes_on_poll_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    start_r = MagicMock()
    start_r.status_code = 200
    start_r.json.return_value = {
        "verification_uri": "https://app.example/auth/device?code=ABCD",
        "user_code": "ABCD-EFGH",
        "device_code": "dev-code",
        "interval": 1,
        "expires_in": 600,
    }
    poll_r = MagicMock()
    poll_r.status_code = 200
    poll_r.json.return_value = {"status": "completed"}

    post_client = MagicMock()
    post_client.post.return_value = start_r
    post_cm = MagicMock()
    post_cm.__enter__.return_value = post_client
    post_cm.__exit__.return_value = None

    poll_client = MagicMock()
    poll_client.get.return_value = poll_r
    poll_cm = MagicMock()
    poll_cm.__enter__.return_value = poll_client
    poll_cm.__exit__.return_value = None

    monkeypatch.setattr(
        "sellerclaw_agent.cli.httpx.Client",
        MagicMock(side_effect=[post_cm, poll_cm]),
    )
    monkeypatch.setattr("sellerclaw_agent.cli.webbrowser.open", lambda _uri: None)
    _device_flow(Console(record=True), "http://127.0.0.1:8001")
    poll_client.get.assert_called_once()


def _health_snapshot(
    *,
    session_connected: bool,
    ping_last_success_at: str | None,
    chat_connected: bool = True,
    ping_last_error: str | None = None,
) -> dict[str, object]:
    return {
        "status": "healthy",
        "edge_ping_enabled": True,
        "session": {"connected": session_connected, "agent_instance_id": "abc"},
        "tasks": {
            "ping_loop": {
                "alive": True,
                "last_success_at": ping_last_success_at,
                "consecutive_errors": 0 if ping_last_error is None else 1,
                "last_error": ping_last_error,
                "restart_count": 0,
                "current_command_id": None,
                "connected": None,
            },
            "command_executor": {
                "alive": True,
                "last_success_at": None,
                "consecutive_errors": 0,
                "last_error": None,
                "restart_count": 0,
                "current_command_id": None,
                "connected": None,
            },
            "chat_sse": {
                "alive": True,
                "last_success_at": None,
                "consecutive_errors": 0,
                "last_error": None,
                "restart_count": 0,
                "current_command_id": None,
                "connected": chat_connected,
            },
        },
        "openclaw": {},
    }


def test_wait_for_cloud_live_returns_true_when_ping_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = _health_snapshot(
        session_connected=True,
        ping_last_success_at="2026-04-21T00:00:00Z",
        chat_connected=True,
    )
    monkeypatch.setattr("sellerclaw_agent.cli._get_health_snapshot", lambda _u: snap)
    ok, reason, chat_ok = _wait_for_cloud_live(
        "http://127.0.0.1:8001",
        Console(record=True),
        timeout_s=1.0,
    )
    assert ok is True
    assert reason is None
    assert chat_ok is True


def test_wait_for_cloud_live_detects_chat_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = _health_snapshot(
        session_connected=True,
        ping_last_success_at="2026-04-21T00:00:00Z",
        chat_connected=False,
    )
    monkeypatch.setattr("sellerclaw_agent.cli._get_health_snapshot", lambda _u: snap)
    ok, _reason, chat_ok = _wait_for_cloud_live(
        "http://127.0.0.1:8001",
        Console(record=True),
        timeout_s=1.0,
    )
    assert ok is True
    assert chat_ok is False


def test_wait_for_cloud_live_times_out_without_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = _health_snapshot(
        session_connected=False,
        ping_last_success_at=None,
        ping_last_error="cloud unreachable: timeout",
    )
    monkeypatch.setattr("sellerclaw_agent.cli._get_health_snapshot", lambda _u: snap)
    monkeypatch.setattr("sellerclaw_agent.cli.time.sleep", lambda _s: None)
    ok, reason, _chat = _wait_for_cloud_live(
        "http://127.0.0.1:8001",
        Console(record=True),
        timeout_s=0.1,
    )
    assert ok is False
    assert reason is not None
    assert "timeout" in reason or "edge session" in reason


def test_wait_for_cloud_live_times_out_without_ping_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = _health_snapshot(
        session_connected=True,
        ping_last_success_at=None,
        ping_last_error="cloud 502 bad gateway",
    )
    monkeypatch.setattr("sellerclaw_agent.cli._get_health_snapshot", lambda _u: snap)
    monkeypatch.setattr("sellerclaw_agent.cli.time.sleep", lambda _s: None)
    ok, reason, _chat = _wait_for_cloud_live(
        "http://127.0.0.1:8001",
        Console(record=True),
        timeout_s=0.1,
    )
    assert ok is False
    assert reason is not None
    assert "502" in reason or "heartbeat" in reason


def test_wait_for_cloud_live_tolerates_health_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _fake_snap(_u: str) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return _health_snapshot(
            session_connected=True,
            ping_last_success_at="2026-04-21T00:00:00Z",
        )

    monkeypatch.setattr("sellerclaw_agent.cli._get_health_snapshot", _fake_snap)
    monkeypatch.setattr("sellerclaw_agent.cli.time.sleep", lambda _s: None)
    ok, _reason, _chat = _wait_for_cloud_live(
        "http://127.0.0.1:8001",
        Console(record=True),
        timeout_s=5.0,
    )
    assert ok is True
    assert calls["n"] >= 2
