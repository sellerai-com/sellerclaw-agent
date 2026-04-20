from __future__ import annotations

import asyncio
import random


async def sleep_until(stop: asyncio.Event, seconds: float) -> None:
    """Sleep for ``seconds`` unless ``stop`` is set first."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return


def ping_interval_success() -> float:
    """Base ~10s heartbeat with jitter (reduces thundering herd)."""
    return 10.0 + random.uniform(-1.5, 1.5)


def ping_interval_after_error(consecutive_errors: int) -> float:
    """Exponential backoff capped at 300s + small jitter after failures."""
    if consecutive_errors <= 0:
        return ping_interval_success()
    exp = min(10.0 * (2 ** (consecutive_errors - 1)), 300.0)
    return exp + random.uniform(0.0, 3.0)


def ping_interval_when_suspended() -> float:
    """Long sleep while server reports agent_suspended (403); avoid hammering API."""
    return 180.0 + random.uniform(0.0, 30.0)


def sse_interval_after_error(previous_backoff: float, *, max_seconds: float = 30.0) -> float:
    """Double previous SSE error backoff up to ``max_seconds``, with jitter."""
    nxt = min(max_seconds, max(2.0, previous_backoff * 2.0))
    return nxt + random.uniform(0.0, 0.5)
