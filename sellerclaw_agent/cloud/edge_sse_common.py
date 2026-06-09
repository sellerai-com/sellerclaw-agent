"""Shared helpers for the edge → cloud SSE listeners (chat, hooks, unified)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from sellerclaw_agent.cloud.exceptions import (
    CloudAgentSuspendedError,
    CloudAuthError,
    CloudConnectionError,
    CloudConnectionInactiveError,
    CloudSessionInvalidatedError,
    agent_api_error_code,
)
from sellerclaw_agent.cloud.supervisor_manager import SupervisorContainerManager

# Throttle supervisord probes while draining SSE (each probe is a ``supervisorctl`` subprocess).
OPENCLAW_PROBE_TTL_SEC = 5.0
# When the probe itself fails, keep the stale "error" verdict longer to avoid hot loops.
OPENCLAW_PROBE_ERROR_TTL_SEC = 15.0

# (running, status, error) — whether OpenClaw is up, plus the raw status/error for logging.
OpenClawGate = Callable[[], Awaitable[tuple[bool, str, str | None]]]


def make_openclaw_gate(supervisor_mgr: SupervisorContainerManager) -> OpenClawGate:
    """Build a cached OpenClaw-readiness probe shared by all edge SSE consumers.

    Caches the verdict for a short TTL so a burst of inbound events doesn't spawn a
    ``supervisorctl`` subprocess per event.
    """
    probe_at = 0.0
    probe_status = ""
    probe_err: str | None = None

    async def _gate() -> tuple[bool, str, str | None]:
        nonlocal probe_at, probe_status, probe_err
        now_m = time.monotonic()
        ttl = OPENCLAW_PROBE_ERROR_TTL_SEC if probe_status == "error" else OPENCLAW_PROBE_TTL_SEC
        if probe_status and now_m - probe_at < ttl:
            return probe_status == "running", probe_status, probe_err
        probe_at = now_m
        probe_status, probe_err = await asyncio.to_thread(supervisor_mgr.probe_openclaw_status)
        return probe_status == "running", probe_status, probe_err

    return _gate


def api_detail_message(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        msg = detail.get("message")
        if isinstance(msg, str):
            return msg
    if isinstance(detail, str):
        return detail
    return "Request failed"


async def error_response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        await response.aread()
    except Exception:
        return {}
    try:
        raw = response.json()
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


async def raise_for_edge_sse_status(response: httpx.Response, *, label: str) -> None:
    """Map an edge-SSE GET response status to the right Cloud* exception.

    ``label`` namespaces the raised messages (``{label}_forbidden`` etc.) so the per-stream
    reconnect loops keep their existing string checks.
    """
    if response.status_code == 401:
        raise CloudAuthError(f"{label}_unauthorized", status_code=401)
    if response.status_code == 403:
        err_body = await error_response_json(response)
        code = agent_api_error_code(err_body)
        if code == "agent_suspended":
            raise CloudAgentSuspendedError(api_detail_message(err_body))
        if code == "agent_session_invalidated":
            raise CloudSessionInvalidatedError(
                api_detail_message(err_body) or f"{label}_session_invalidated",
                status_code=403,
            )
        if code in ("agent_connection_inactive", "agent_connection_not_found"):
            raise CloudConnectionInactiveError(
                api_detail_message(err_body) or f"{label}_connection_inactive"
            )
        raise CloudConnectionError(f"{label}_forbidden")
    response.raise_for_status()
