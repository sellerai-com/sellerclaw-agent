from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sellerclaw_agent.cloud.exceptions import CloudAgentSuspendedError
from sellerclaw_agent.server.edge_commands import CommandResultStore
from sellerclaw_agent.server.ping_loop import run_edge_ping_loop
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit


async def test_ping_loop_suspended_uses_long_sleep_and_keeps_session(
    monkeypatch,
    tmp_path,
) -> None:
    """403 agent_suspended: no credential/session clear; backoff from ping_interval_when_suspended."""
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))

    stop = asyncio.Event()
    registry = EdgeRuntimeRegistry()
    command_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    result_store = CommandResultStore()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test_supervisor")

    inst_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    class _Sess:
        agent_instance_id = inst_id
        protocol_version = 1

    session_storage = MagicMock()
    session_storage.load = MagicMock(return_value=_Sess())
    session_storage.clear = MagicMock()
    creds_storage = MagicMock()
    creds_storage.load = MagicMock(return_value=object())

    mock_client = MagicMock()
    mock_client.connect = AsyncMock(
        return_value=MagicMock(agent_instance_id=inst_id),
    )
    mock_client.ping = AsyncMock(side_effect=CloudAgentSuspendedError("suspended"))

    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.CredentialsStorage",
        MagicMock(return_value=creds_storage),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.EdgeSessionStorage",
        MagicMock(return_value=session_storage),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.SellerClawConnectionClient",
        MagicMock(return_value=mock_client),
    )
    fake_mgr = MagicMock()
    fake_mgr.probe_openclaw_status = MagicMock(return_value=("stopped", None))
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.create_supervisor_manager",
        MagicMock(return_value=fake_mgr),
    )

    sleep_calls: list[float] = []

    async def _rec_sleep(_stop: asyncio.Event, seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 1:
            stop.set()
        await asyncio.sleep(0)

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.sleep_until", _rec_sleep)

    ping_task = asyncio.create_task(
        run_edge_ping_loop(
            stop,
            command_queue=command_queue,
            result_store=result_store,
            supervisor_executor=executor,
            registry=registry,
        ),
    )

    await asyncio.wait_for(ping_task, timeout=2.0)
    executor.shutdown(wait=False, cancel_futures=True)

    assert sleep_calls, "expected suspended backoff sleep"
    assert 28.0 <= sleep_calls[0] <= 30.0
    session_storage.clear.assert_not_called()
    creds_storage.load.assert_called()
