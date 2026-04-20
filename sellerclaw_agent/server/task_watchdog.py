from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

_log = structlog.get_logger(__name__)

MAX_TASK_RESTARTS = 3


def start_watched_background(
    factory: Callable[[], Awaitable[None]],
    *,
    name: str,
    stop: asyncio.Event,
    registry: EdgeRuntimeRegistry,
) -> dict[str, Any]:
    """Run ``factory()`` as a background task; restart on crash up to ``MAX_TASK_RESTARTS``."""
    holder: dict[str, Any] = {"task": None, "restart_count": 0}

    def spawn() -> None:
        async def _runner() -> None:
            await factory()

        task = asyncio.create_task(_runner(), name=name)
        holder["task"] = task

        def _done(t: asyncio.Task[None]) -> None:
            if stop.is_set():
                return
            if t.cancelled():
                return
            exc = t.exception()
            if exc is None:
                return
            holder["restart_count"] = holder.get("restart_count", 0) + 1
            registry.increment_restart(name)
            _log.error(
                "edge_background_task_crashed",
                task=name,
                restart=holder["restart_count"],
                error=str(exc),
            )
            if holder["restart_count"] <= MAX_TASK_RESTARTS:
                spawn()
            else:
                registry.mark_task_alive(name, alive=False)
                _log.error("edge_background_task_max_restarts", task=name)

        task.add_done_callback(_done)

    spawn()
    return holder
