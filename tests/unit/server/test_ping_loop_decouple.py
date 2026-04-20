from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sellerclaw_agent.server.edge_commands import CommandResultStore, RemoteCommandWork, run_edge_command_executor_loop
from sellerclaw_agent.server.ping_loop import run_edge_ping_loop
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit


async def test_ping_loop_enqueues_command_and_executor_fills_result_store(
    monkeypatch,
    tmp_path,
) -> None:
    """Ping loop must not block on supervisor work: executor fills ``CommandResultStore``."""
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))

    stop = asyncio.Event()
    registry = EdgeRuntimeRegistry()
    command_queue: asyncio.Queue[RemoteCommandWork] = asyncio.Queue(maxsize=8)
    result_store = CommandResultStore()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test_supervisor")

    cmd_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    inst_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    issued = datetime.now(tz=UTC)

    ping_calls = {"n": 0}

    class _Sess:
        agent_instance_id = inst_id
        protocol_version = 1

    class _Ping:
        def __init__(self, pending: object | None) -> None:
            self.pending_command = pending

    class _Pending:
        command_id = cmd_id
        command_type = "stop"
        issued_at = issued

    mock_client = MagicMock()
    mock_client.connect = AsyncMock(
        return_value=MagicMock(agent_instance_id=inst_id),
    )

    async def _ping(**_kwargs: object) -> _Ping:
        ping_calls["n"] += 1
        if ping_calls["n"] == 1:
            return _Ping(_Pending())
        return _Ping(None)

    mock_client.ping = AsyncMock(side_effect=_ping)

    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.CredentialsStorage",
        MagicMock(return_value=MagicMock(load=MagicMock(return_value=object()))),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.EdgeSessionStorage",
        MagicMock(return_value=MagicMock(load=MagicMock(return_value=_Sess()))),
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

    monkeypatch.setattr(
        "sellerclaw_agent.server.edge_commands.create_supervisor_manager",
        MagicMock(return_value=fake_mgr),
    )
    fake_mgr.stop = MagicMock(return_value=("completed", None))

    monkeypatch.setattr(
        "sellerclaw_agent.server.edge_commands.SellerClawConnectionClient",
        MagicMock(return_value=MagicMock(fetch_edge_manifest=AsyncMock())),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.edge_commands.CredentialsStorage",
        MagicMock(return_value=MagicMock(load=MagicMock(return_value=object()))),
    )

    async def _fast_sleep(_stop: asyncio.Event, _seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.sleep_until", _fast_sleep)
    monkeypatch.setattr("sellerclaw_agent.server.edge_commands.CommandHistoryStorage", MagicMock())

    exec_task = asyncio.create_task(
        run_edge_command_executor_loop(
            stop=stop,
            command_queue=command_queue,
            result_store=result_store,
            supervisor_executor=executor,
            registry=registry,
        ),
    )
    ping_task = asyncio.create_task(
        run_edge_ping_loop(
            stop,
            command_queue=command_queue,
            result_store=result_store,
            supervisor_executor=executor,
            registry=registry,
        ),
    )

    for _ in range(200):
        await asyncio.sleep(0.01)
        if fake_mgr.stop.called and ping_calls["n"] >= 2:
            break
    assert fake_mgr.stop.called
    assert ping_calls["n"] >= 2

    stop.set()
    await asyncio.wait_for(asyncio.gather(ping_task, exec_task), timeout=2.0)
    executor.shutdown(wait=False, cancel_futures=True)
