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
    """Exponential backoff capped at 30s + small jitter after failures."""
    if consecutive_errors <= 0:
        return ping_interval_success()
    exp = min(10.0 * (2 ** (consecutive_errors - 1)), 30.0)
    return min(30.0, exp + random.uniform(0.0, 0.5))


def ping_interval_when_suspended() -> float:
    """Long sleep while server reports agent_suspended (403); avoid hammering API."""
    return 28.0 + random.uniform(0.0, 2.0)


# Reconnect-delay ceilings, kept deliberately small: a dropped stream means the user's agent
# is offline, so we must reconnect quickly. The jitter window only has to be wide enough to
# spread the fleet's reconnects under the cloud's pooled-DB capacity — ~15s is plenty for that
# and is the worst case a user waits only after repeated failures (an isolated drop retries in
# ~0-2s). The cloud-side concurrency limit absorbs any residual burst.
_SSE_BACKOFF_BASE = 2.0
_SSE_BACKOFF_MAX = 15.0
_SSE_OVERLOAD_MAX = 20.0
_SSE_CLEAN_RECONNECT_MAX = 5.0
_OVERLOAD_STATUS = frozenset({429, 500, 502, 503, 504})


def is_overload_status(status_code: int | None) -> bool:
    """True when an HTTP status means the cloud is shedding load (429 / 5xx)."""
    return status_code in _OVERLOAD_STATUS


def sse_reconnect_sleep(ceiling: float) -> float:
    """FULL-jitter delay in ``[0, ceiling]`` before reconnecting an SSE stream.

    Full jitter (vs the old ``+0..0.5s``) is what actually de-synchronises a fleet-wide
    reconnect: when the cloud drops every agent's stream at once, they spread across the
    whole window instead of a sub-second spike that immediately re-overwhelms the server.
    """
    return random.uniform(0.0, max(0.0, ceiling))


def sse_clean_reconnect_sleep() -> float:
    """Short full-jitter delay after a *clean* stream close.

    A clean close usually means the cloud restarted/redeployed and dropped every agent at
    once. Reconnecting immediately (the old behaviour) stampedes; a few seconds of jitter
    spreads the fleet out.
    """
    return random.uniform(0.0, _SSE_CLEAN_RECONNECT_MAX)


def sse_backoff_ceiling(previous_ceiling: float, *, overloaded: bool = False) -> float:
    """Grow the SSE reconnect ceiling (deterministic exponential doubling), capped.

    ``overloaded`` — the cloud answered 429/5xx, i.e. it is actively shedding load (e.g. a
    proxy concurrency limit) — raises the cap so the fleet backs off harder instead of
    re-piling on a struggling server. Without this a cloud-side hard_limit just converts the
    reconnect storm into a 503-retry storm.
    """
    cap = _SSE_OVERLOAD_MAX if overloaded else _SSE_BACKOFF_MAX
    return min(cap, max(_SSE_BACKOFF_BASE, previous_ceiling * 2.0))
