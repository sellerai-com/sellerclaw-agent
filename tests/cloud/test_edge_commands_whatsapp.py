from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from openclaw_diagnostics.gateway import GatewayError
from sellerclaw_agent.cloud.connection_client import WhatsappLinkRequest
from sellerclaw_agent.cloud.whatsapp_link import WhatsappLinkRunner
from sellerclaw_agent.server import edge_commands
from sellerclaw_agent.server.edge_commands import _execute_remote_command, _logout_whatsapp

from tests.cloud.conftest import FakeWhatsappGateway

pytestmark = pytest.mark.unit

_LOGIN_ID = UUID("33333333-3333-4333-8333-333333333333")


class _RecordingManager:
    def __init__(self) -> None:
        self.started: list[WhatsappLinkRunner] = []
        self.stopped = 0

    def start(self, runner: WhatsappLinkRunner) -> None:
        self.started.append(runner)

    def stop(self) -> None:
        self.stopped += 1


@pytest.fixture
def link_manager(monkeypatch: pytest.MonkeyPatch) -> _RecordingManager:
    manager = _RecordingManager()
    monkeypatch.setattr(edge_commands, "get_whatsapp_link_manager", lambda: manager)
    return manager


async def _run(cmd_type: str, client: MagicMock, tmp_path: Path) -> tuple[str, str | None]:
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_cmd_wa")
    try:
        return await _execute_remote_command(
            loop=asyncio.get_running_loop(),
            executor=executor,
            cmd_type=cmd_type,
            client=client,
            data_dir=tmp_path,
            container_mgr=MagicMock(),
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


async def test_whatsapp_link_starts_the_runner_and_acks_at_once(
    tmp_path: Path,
    link_manager: _RecordingManager,
) -> None:
    client = MagicMock()
    client.fetch_whatsapp_link = AsyncMock(return_value=WhatsappLinkRequest(login_id=_LOGIN_ID, force=True))

    outcome, error = await _run("whatsapp_link", client, tmp_path)

    assert (outcome, error) == ("completed", None)
    assert len(link_manager.started) == 1
    runner = link_manager.started[0]
    assert runner.login_id == _LOGIN_ID
    assert runner.force is True
    assert runner.cloud is client
    assert runner.on_linked is not None


@pytest.mark.parametrize(
    ("fetch", "expected_outcome", "expected_error"),
    [
        pytest.param(AsyncMock(return_value=None), "rejected", "No open WhatsApp link attempt on the cloud.", id="no-attempt"),
        pytest.param(AsyncMock(side_effect=RuntimeError("boom")), "failed", "WhatsApp link fetch failed: boom", id="fetch-error"),
    ],
)
async def test_whatsapp_link_without_an_attempt(
    tmp_path: Path,
    link_manager: _RecordingManager,
    fetch: AsyncMock,
    expected_outcome: str,
    expected_error: str,
) -> None:
    client = MagicMock()
    client.fetch_whatsapp_link = fetch

    outcome, error = await _run("whatsapp_link", client, tmp_path)

    assert (outcome, error) == (expected_outcome, expected_error)
    assert link_manager.started == []


@pytest.mark.parametrize(
    "logout_answer",
    [
        pytest.param({"cleared": True, "loggedOut": True}, id="plugin-loaded"),
        pytest.param(GatewayError("channels.logout failed: INVALID_REQUEST: unknown channel: whatsapp"), id="plugin-not-loaded"),
    ],
)
async def test_whatsapp_logout_unlinks_and_drops_both_copies(
    tmp_path: Path,
    link_manager: _RecordingManager,
    fake_whatsapp_gateway: FakeWhatsappGateway,
    make_whatsapp_session_dir: Callable[..., Path],
    logout_answer: object,
) -> None:
    make_whatsapp_session_dir(tmp_path)
    fake_whatsapp_gateway.script("channels.logout", logout_answer)
    client = MagicMock()
    client.delete_whatsapp_session = AsyncMock(return_value=None)

    outcome, error = await _logout_whatsapp(
        client=client,
        gateway_factory=fake_whatsapp_gateway.factory,
        state_dir=tmp_path,
    )

    assert (outcome, error) == ("completed", None)
    assert fake_whatsapp_gateway.calls == [("channels.logout", {"channel": "whatsapp"}, 20.0)]
    assert not (tmp_path / "credentials" / "whatsapp").exists()
    client.delete_whatsapp_session.assert_awaited_once()
    assert link_manager.stopped == 1


@pytest.mark.usefixtures("link_manager")
async def test_whatsapp_logout_survives_a_cloud_failure(
    tmp_path: Path,
    fake_whatsapp_gateway: FakeWhatsappGateway,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    """The cloud deletes its copy on disconnect anyway; the local logout must still finish."""
    make_whatsapp_session_dir(tmp_path)
    fake_whatsapp_gateway.unreachable = True
    client = MagicMock()
    client.delete_whatsapp_session = AsyncMock(side_effect=RuntimeError("cloud down"))

    outcome, _error = await _logout_whatsapp(
        client=client,
        gateway_factory=fake_whatsapp_gateway.factory,
        state_dir=tmp_path,
    )

    assert outcome == "completed"
    assert not (tmp_path / "credentials" / "whatsapp").exists()


@pytest.mark.usefixtures("link_manager")
async def test_whatsapp_logout_keeps_the_files_when_the_plugin_did_not_answer(
    tmp_path: Path,
    fake_whatsapp_gateway: FakeWhatsappGateway,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    """A live socket would rewrite deleted files at once; the command fails instead."""
    make_whatsapp_session_dir(tmp_path)
    fake_whatsapp_gateway.script("channels.logout", GatewayError("channels.logout timed out after 20s"))
    client = MagicMock()
    client.delete_whatsapp_session = AsyncMock(return_value=None)

    outcome, error = await _logout_whatsapp(
        client=client,
        gateway_factory=fake_whatsapp_gateway.factory,
        state_dir=tmp_path,
    )

    assert outcome == "failed"
    assert error == "WhatsApp logout failed: channels.logout timed out after 20s"
    assert (tmp_path / "credentials" / "whatsapp" / "default" / "creds.json").is_file()
    client.delete_whatsapp_session.assert_not_awaited()
