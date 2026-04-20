from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest
from rich.console import Console
from sellerclaw_agent.cli import (
    _connect_password,
    _device_flow,
    agent_base_url,
    agent_root,
    main,
    parse_command,
    wait_for_agent,
)

pytestmark = pytest.mark.unit


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


def test_agent_base_url_strips_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELLERCLAW_AGENT_URL", "http://example:9/")
    assert agent_base_url() == "http://example:9"


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
