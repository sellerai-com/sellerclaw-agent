from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sellerclaw_agent.server.edge_commands import (
    CommandResultStore,
    CompletedRemoteCommand,
    RemoteCommandWork,
)

pytestmark = pytest.mark.unit


async def test_command_result_store_roundtrip() -> None:
    store = CommandResultStore()
    assert await store.get_pending_ack() is None
    wid = UUID("11111111-1111-4111-8111-111111111111")
    iid = UUID("22222222-2222-4222-8222-222222222222")
    work = RemoteCommandWork(
        command_id=wid,
        command_type="start",
        issued_at=datetime.now(tz=UTC),
        received_at_iso="2026-01-01T00:00:00+00:00",
        instance_id=iid,
        protocol_version=1,
    )
    done = CompletedRemoteCommand(
        work=work,
        outcome="completed",
        error=None,
        executed_at_iso="2026-01-01T00:00:01+00:00",
    )
    await store.set_pending_ack(done)
    got = await store.get_pending_ack()
    assert got is not None
    assert got.outcome == "completed"
    assert got.work.command_id == wid
    await store.clear_pending_ack()
    assert await store.get_pending_ack() is None


async def test_command_result_store_second_set_waits_for_clear() -> None:
    store = CommandResultStore()
    iid = UUID("22222222-2222-4222-8222-222222222222")
    issued = datetime.now(tz=UTC)

    def _work(cid: UUID) -> RemoteCommandWork:
        return RemoteCommandWork(
            command_id=cid,
            command_type="stop",
            issued_at=issued,
            received_at_iso="2026-01-01T00:00:00+00:00",
            instance_id=iid,
            protocol_version=1,
        )

    wid1 = UUID("11111111-1111-4111-8111-111111111111")
    wid2 = UUID("33333333-3333-4333-8333-333333333333")
    first = CompletedRemoteCommand(
        work=_work(wid1),
        outcome="completed",
        error=None,
        executed_at_iso="2026-01-01T00:00:01+00:00",
    )
    second = CompletedRemoteCommand(
        work=_work(wid2),
        outcome="failed",
        error="x",
        executed_at_iso="2026-01-01T00:00:02+00:00",
    )
    await store.set_pending_ack(first)

    setter = asyncio.create_task(store.set_pending_ack(second))
    await asyncio.sleep(0.05)
    assert not setter.done()
    mid = await store.get_pending_ack()
    assert mid is not None
    assert mid.work.command_id == wid1

    await store.clear_pending_ack()
    await asyncio.wait_for(setter, timeout=1.0)
    pending = await store.get_pending_ack()
    assert pending is not None
    assert pending.work.command_id == wid2
    assert pending.outcome == "failed"
    assert pending.error == "x"
