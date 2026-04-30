from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sellerclaw_agent.server.edge_commands import (
    CommandResultStore,
    CompletedRemoteCommand,
    RemoteCommandWork,
)
from sellerclaw_agent.server.ping_loop import run_edge_ping_loop
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit


def _make_pending_ack(*, instance_id: UUID, command_id: UUID) -> CompletedRemoteCommand:
    work = RemoteCommandWork(
        command_id=command_id,
        command_type="start",
        issued_at=datetime.now(tz=UTC),
        received_at_iso="2026-01-01T00:00:00+00:00",
        instance_id=instance_id,
        protocol_version=1,
    )
    return CompletedRemoteCommand(
        work=work,
        outcome="completed",
        error=None,
        executed_at_iso="2026-01-01T00:00:01+00:00",
    )


async def _run_until_ack_dropped(
    monkeypatch,
    tmp_path,
    *,
    session_load_return: object,
    pending: CompletedRemoteCommand,
) -> tuple[CommandResultStore, MagicMock, MagicMock, MagicMock]:
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))

    stop = asyncio.Event()
    registry = EdgeRuntimeRegistry()
    command_queue: asyncio.Queue = asyncio.Queue(maxsize=8)

    class _StopOnClearStore(CommandResultStore):
        async def clear_pending_ack(self) -> None:
            await super().clear_pending_ack()
            stop.set()

    result_store = _StopOnClearStore()
    await result_store.set_pending_ack(pending)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test_supervisor")

    session_storage = MagicMock()
    session_storage.load = MagicMock(return_value=session_load_return)
    session_storage.clear = MagicMock()
    session_storage.save = MagicMock()
    creds_storage = MagicMock()
    creds_storage.load = MagicMock(return_value=MagicMock(agent_token="test_agent_token"))
    creds_storage.clear = MagicMock()

    mock_client = MagicMock()
    mock_client.connect = AsyncMock(side_effect=AssertionError("connect should not run"))
    mock_client.ping = AsyncMock(side_effect=AssertionError("ping should not run for stale ack"))

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

    async def _noop_sleep(_stop: asyncio.Event, seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.sleep_until", _noop_sleep)

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
    return result_store, creds_storage, session_storage, mock_client


async def test_pending_ack_dropped_when_session_lost(monkeypatch, tmp_path) -> None:
    """Session gone before ack flush: drop the ack instead of looping forever.

    Regression: previously the ping loop spammed
    ``edge_command_ack_skipped_no_session`` because ``_flush_command_ack``
    returned False on missing session, the caller backed off, and the
    pending_ack was never cleared.
    """
    inst_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    cmd_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    pending = _make_pending_ack(instance_id=inst_id, command_id=cmd_id)

    store, creds_storage, _session_storage, client = await _run_until_ack_dropped(
        monkeypatch,
        tmp_path,
        session_load_return=None,
        pending=pending,
    )

    assert await store.get_pending_ack() is None
    creds_storage.clear.assert_not_called()
    client.ping.assert_not_called()
    client.connect.assert_not_called()


async def test_pending_ack_dropped_when_session_id_mismatches(monkeypatch, tmp_path) -> None:
    """Session rotated since the command ran: drop the ack — new instance_id ≠ old."""
    old_inst_id = UUID("11111111-1111-4111-8111-111111111111")
    new_inst_id = UUID("22222222-2222-4222-8222-222222222222")
    cmd_id = UUID("33333333-3333-4333-8333-333333333333")
    pending = _make_pending_ack(instance_id=old_inst_id, command_id=cmd_id)

    class _Sess:
        agent_instance_id = new_inst_id
        protocol_version = 1

    store, creds_storage, _session_storage, client = await _run_until_ack_dropped(
        monkeypatch,
        tmp_path,
        session_load_return=_Sess(),
        pending=pending,
    )

    assert await store.get_pending_ack() is None
    creds_storage.clear.assert_not_called()
    client.ping.assert_not_called()
