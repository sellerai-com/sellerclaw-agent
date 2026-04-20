from __future__ import annotations

import asyncio

import pytest
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry
from sellerclaw_agent.server.task_watchdog import MAX_TASK_RESTARTS, start_watched_background

pytestmark = pytest.mark.unit


async def test_watchdog_restarts_after_one_crash() -> None:
    stop = asyncio.Event()
    registry = EdgeRuntimeRegistry()
    calls = {"n": 0}

    async def flaky() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "boom"
            raise RuntimeError(msg)
        await stop.wait()

    holder = start_watched_background(flaky, name="ping_loop", stop=stop, registry=registry)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if calls["n"] >= 2:
            break
    assert calls["n"] == 2
    assert holder["restart_count"] == 1
    stop.set()
    task = holder["task"]
    assert isinstance(task, asyncio.Task)
    await asyncio.wait_for(task, timeout=1.0)


async def test_watchdog_stops_after_max_restarts() -> None:
    stop = asyncio.Event()
    registry = EdgeRuntimeRegistry()

    async def always_boom() -> None:
        raise RuntimeError("boom")

    holder = start_watched_background(always_boom, name="ping_loop", stop=stop, registry=registry)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if holder["restart_count"] > MAX_TASK_RESTARTS:
            break
    assert holder["restart_count"] == MAX_TASK_RESTARTS + 1
    snap = registry.snapshot_tasks()
    assert snap["ping_loop"]["alive"] is False
