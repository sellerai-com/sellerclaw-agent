from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sellerclaw_agent.cloud.cron_standby import CronStandby
from sellerclaw_agent.cloud.exceptions import (
    CloudConnectionAlreadyActiveError,
    CloudSessionInvalidatedError,
)
from sellerclaw_agent.server.edge_commands import CommandResultStore
from sellerclaw_agent.server.ping_loop import AGENT_PROTOCOL_VERSION, run_edge_ping_loop
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit

_INSTANCE = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


class _FakeStandbyPlugin:
    def __init__(self) -> None:
        self.requested: list[bool] = []

    async def post_cron_standby_json(self, *, standby: bool) -> tuple[bool, dict[str, Any]]:
        self.requested.append(standby)
        return True, {"ok": True}


async def _run_loop(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: MagicMock,
    session: Any,
    openclaw_status: str,
    standby: CronStandby,
    iterations: int = 1,
) -> MagicMock:
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    stop = asyncio.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test_supervisor")

    session_storage = MagicMock()
    session_storage.load = MagicMock(return_value=session)
    creds_storage = MagicMock()
    creds_storage.load = MagicMock(return_value=MagicMock(agent_token="test_agent_token"))
    fake_mgr = MagicMock()
    fake_mgr.probe_openclaw_status = MagicMock(return_value=(openclaw_status, None))
    fake_mgr.probe_browser_status = MagicMock(
        return_value=MagicMock(status="stopped", kasmvnc_running=False, chrome_running=False, error=None, pages=())
    )
    activity_reader = MagicMock()
    activity_reader.probe = AsyncMock(return_value=None)

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.CredentialsStorage", MagicMock(return_value=creds_storage))
    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.EdgeSessionStorage", MagicMock(return_value=session_storage))
    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.SellerClawConnectionClient", MagicMock(return_value=client))
    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.create_supervisor_manager", MagicMock(return_value=fake_mgr))
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.create_agent_activity_reader", MagicMock(return_value=activity_reader)
    )
    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.agent_activity_ping_payload", MagicMock(return_value=None))

    sleeps: list[float] = []

    async def _rec_sleep(_stop: asyncio.Event, seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= iterations:
            stop.set()
        await asyncio.sleep(0)

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.sleep_until", _rec_sleep)

    await asyncio.wait_for(
        run_edge_ping_loop(
            stop,
            command_queue=asyncio.Queue(maxsize=8),
            result_store=CommandResultStore(),
            supervisor_executor=executor,
            registry=EdgeRuntimeRegistry(),
            cron_standby=standby,
        ),
        timeout=2.0,
    )
    executor.shutdown(wait=False, cancel_futures=True)
    return session_storage


async def test_a_refused_connect_puts_cron_on_standby(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Another instance holds the session: this one's jobs go off before two machines run them."""
    plugin = _FakeStandbyPlugin()
    standby = CronStandby(tmp_path, plugin_factory=lambda: plugin)
    client = MagicMock()
    client.connect = AsyncMock(
        side_effect=CloudConnectionAlreadyActiveError("Another edge agent session is already connected.")
    )
    client.ping = AsyncMock()

    await _run_loop(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        client=client,
        session=None,
        openclaw_status="running",
        standby=standby,
    )

    assert standby.is_standby() is True
    assert plugin.requested == [True]
    client.ping.assert_not_awaited()


async def test_standby_waits_for_a_running_openclaw(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plugin = _FakeStandbyPlugin()
    standby = CronStandby(tmp_path, plugin_factory=lambda: plugin)
    client = MagicMock()
    client.connect = AsyncMock(side_effect=CloudConnectionAlreadyActiveError("taken"))

    await _run_loop(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        client=client,
        session=None,
        openclaw_status="stopped",
        standby=standby,
    )

    assert standby.is_standby() is True
    assert plugin.requested == [], "a stopped OpenClaw runs no jobs and has no plugin to tell"


async def test_an_accepted_ping_releases_standby(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plugin = _FakeStandbyPlugin()
    standby = CronStandby(tmp_path, plugin_factory=lambda: plugin)
    standby.request_standby()

    class _Sess:
        agent_instance_id = _INSTANCE
        protocol_version = AGENT_PROTOCOL_VERSION

    client = MagicMock()
    client.ping = AsyncMock(return_value=MagicMock(pending_command=None))

    await _run_loop(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        client=client,
        session=_Sess(),
        openclaw_status="running",
        standby=standby,
    )

    assert standby.is_standby() is False
    assert plugin.requested == [False]
    assert client.ping.await_args.kwargs["protocol_version"] == 7


async def test_an_invalidated_session_alone_is_not_standby(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A stale session (network blip) reconnects fine; only a refused connect means replaced."""
    plugin = _FakeStandbyPlugin()
    standby = CronStandby(tmp_path, plugin_factory=lambda: plugin)

    class _Sess:
        agent_instance_id = _INSTANCE
        protocol_version = AGENT_PROTOCOL_VERSION

    client = MagicMock()
    client.ping = AsyncMock(side_effect=CloudSessionInvalidatedError("stale", status_code=401))

    session_storage = await _run_loop(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        client=client,
        session=_Sess(),
        openclaw_status="running",
        standby=standby,
    )

    assert standby.is_standby() is False
    assert plugin.requested == []
    session_storage.clear.assert_called_once()
