"""Summarise OpenClaw agent activity for ping payloads, via the gateway control plane.

The gateway is the only thing that knows whether a run is in flight right now: it holds
that in process memory, and `sessions.list` reports it per session as ``hasActiveRun``.
Everything else here — when a session was last touched, which ones are recent, which ended
badly — comes off the same call, so one round trip answers the whole probe.

A gateway we cannot reach is not a failed probe: it means no runs are in flight, because
runs only exist inside that process. We report ``idle`` with the reason attached, so the
cloud's "is it safe to redeploy" check reads it correctly rather than holding forever.

We also tail OpenClaw's stdout/stderr log (mirrored to a file by ``openclaw_start``; see
``OPENCLAW_FILE_LOGS``) for error lines. Sessions only record failures *inside* a run —
startup crashes, OOM, gateway/plugin errors that leave the agent silent never reach a
session, but they do appear in the process log.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Any

import structlog
from openclaw_diagnostics.gateway import (
    GatewayConnection,
    GatewayError,
    GatewayRpc,
    GatewayUnreachableError,
    agent_id_from_session_key,
)

_log = structlog.get_logger(__name__)

_DEFAULT_STATE_DIR = "/home/node/.openclaw"
_SUMMARY_LIMIT = 240

# Rows come back newest-first, and only the recent ones are ever counted as active, so this
# is a safety rail against an unbounded frame rather than a real limit on what we look at.
_SESSION_ROW_LIMIT = 200

# Session run outcomes that mean the run did not deliver. "done" and "running" are the
# healthy states; anything else is worth showing the operator.
_FAILED_RUN_STATUSES = frozenset({"failed", "killed", "timeout"})

# Substrings (case-sensitive) that mark a process-log line as an error worth surfacing.
# Conservative on purpose — plaintext logs are noisy and lowercase "error" appears in URLs.
_LOG_ERROR_TOKENS = (
    "FATAL",
    "ERROR",
    "Error:",
    "Exception",
    "Traceback (most recent call last)",
    "UnhandledPromiseRejection",
    "panic:",
    # Silent-delivery failures: a completed result (image/media, subagent announce)
    # never reaches the requester because waking its session failed. OpenClaw logs
    # these with lowercase "failed" and no ERROR/FATAL, so the run looks healthy
    # (session ends cleanly, no session-level error) while the user gets nothing.
    # These phrases are specific enough to stay low-noise.
    "wake failed",
    "was not woken",
    "could not be woken",
    # Same class, our own side of it: the sellerclaw-ui plugin marks a completion run whose
    # answer never reached the owner. Routine delivery lines share the ``sellerclaw-ui
    # [delivery]`` prefix but not this word, so only the ones that cost an answer ride along.
    "sellerclaw-ui[delivery] undelivered",
)


def _env_float(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class AgentActivityProbe:
    """Snapshot of OpenClaw agent activity as the gateway reports it."""

    state: str  # "working" | "idle" | "no_sessions" | "error"
    last_event_at: str | None  # ISO-8601 UTC of the newest session activity
    idle_seconds: float | None  # seconds since last_event_at
    sessions_total: int  # every session the gateway knows about
    sessions_active: int  # sessions touched within the recent window
    agents: dict[str, int]  # active session count per agent_id (subagents included)
    latest: dict[str, Any] | None  # most recent run/tool event from the audit ledger
    recent_errors: list[dict[str, Any]]  # sessions whose last run did not deliver
    log_errors: list[str]  # recent error lines from the OpenClaw process log (crashes/startup)
    error: str | None  # probe-level failure (e.g. gateway unreachable)


@dataclass
class AgentActivityReader:
    """Turn one ``sessions.list`` snapshot into an :class:`AgentActivityProbe`.

    Stateless across calls: every ``probe()`` opens its own short-lived gateway connection.
    A fresh connect costs a few milliseconds on loopback and removes a whole class of bugs
    — no stale socket to detect, no reconnect ladder, and a gateway that restarted between
    heartbeats is simply the next connect.
    """

    working_window_seconds: float = 30.0
    recent_window_seconds: float = 300.0
    max_recent_errors: int = 5
    log_file: Path | None = None
    log_tail_bytes: int = 64 * 1024
    max_log_errors: int = 5
    connection_factory: Callable[[], GatewayRpc] = field(default=GatewayConnection)

    async def probe(self) -> AgentActivityProbe:
        # Log errors are read independently of sessions: a crash that leaves no session
        # behind is exactly the case we must still report.
        log_errors = await asyncio.to_thread(self._read_log_errors)
        try:
            rows, sessions_total, latest = await self._read_gateway()
        except GatewayError as exc:
            # Runs live in the gateway process. If it will not tell us about them, we cannot
            # claim one is in flight — an idle answer with the reason attached, not a broken
            # probe. The two reasons are worth telling apart: nothing listening is the normal
            # stopped agent, while a gateway that answered and refused is a bug to chase.
            reason = "gateway_unreachable" if isinstance(exc, GatewayUnreachableError) else "gateway_error"
            _log.debug("agent_activity_gateway_unavailable", reason=reason, error=str(exc))
            return AgentActivityProbe(
                state="idle",
                last_event_at=None,
                idle_seconds=None,
                sessions_total=0,
                sessions_active=0,
                agents={},
                latest=None,
                recent_errors=[],
                log_errors=log_errors,
                error=f"{reason}: {str(exc)[:400]}",
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as probe error
            _log.warning("agent_activity_probe_failed", error=str(exc))
            return AgentActivityProbe(
                state="error",
                last_event_at=None,
                idle_seconds=None,
                sessions_total=0,
                sessions_active=0,
                agents={},
                latest=None,
                recent_errors=[],
                log_errors=log_errors,
                error=str(exc)[:500],
            )
        return self._summarise(rows, sessions_total, latest, log_errors)

    async def _read_gateway(self) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
        async with self.connection_factory() as conn:
            # Rows come back pinned-first, then newest `updatedAt` first — the gateway's
            # only ordering, so a plain limit already yields the sessions that matter.
            # Message previews are opt-in (`includeLastMessage`) and stay off: they would
            # be the bulk of the frame and we never look at them.
            payload = await conn.call("sessions.list", {"limit": _SESSION_ROW_LIMIT})
            rows = _rows_of(payload)
            total = payload.get("totalCount") if isinstance(payload, dict) else None
            sessions_total = total if isinstance(total, int) and total >= 0 else len(rows)
            return rows, sessions_total, await self._read_latest_event(conn)

    async def _read_latest_event(self, conn: GatewayRpc) -> dict[str, Any] | None:
        """Newest run/tool event from the audit ledger, or ``None`` if it has none.

        The metadata-only ledger is on by default, but it is explicitly best-effort (its
        write queue may drop events) and an operator can turn it off (``audit.enabled:
        false``) — so a miss here is legitimate and must not cost us the rest of the probe.
        """
        try:
            payload = await conn.call("audit.list", {"limit": 1})
        except GatewayError as exc:
            _log.debug("agent_activity_audit_unavailable", error=str(exc))
            return None
        events = payload.get("events") if isinstance(payload, dict) else None
        event = events[0] if isinstance(events, list) and events else None
        if not isinstance(event, dict):
            return None
        occurred_at = _as_epoch_ms(event.get("occurredAt"))
        age_seconds = None if occurred_at is None else round(max(0.0, time() - occurred_at / 1000), 1)
        return {
            "agent_id": _as_text(event.get("agentId")) or "unknown",
            "session_key": _as_text(event.get("sessionKey")),
            "type": _as_text(event.get("action")) or _as_text(event.get("kind")),
            "tool": _as_text(event.get("toolName")),
            "age_seconds": age_seconds,
        }

    def _summarise(
        self,
        rows: list[dict[str, Any]],
        sessions_total: int,
        latest: dict[str, Any] | None,
        log_errors: list[str],
    ) -> AgentActivityProbe:
        if sessions_total == 0 and not rows:
            return AgentActivityProbe(
                state="no_sessions",
                last_event_at=None,
                idle_seconds=None,
                sessions_total=0,
                sessions_active=0,
                agents={},
                latest=latest,
                recent_errors=[],
                log_errors=log_errors,
                error=None,
            )

        now = time()
        stamped = [(row, _row_activity_ms(row)) for row in rows]
        newest_ms = max((ms for _, ms in stamped if ms is not None), default=None)
        idle_seconds = None if newest_ms is None else round(max(0.0, now - newest_ms / 1000), 1)

        active = [
            (row, ms)
            for row, ms in stamped
            if ms is not None and (now - ms / 1000) <= self.recent_window_seconds
        ]
        agents: dict[str, int] = {}
        for row, _ in active:
            agent_id = _row_agent_id(row)
            agents[agent_id] = agents.get(agent_id, 0) + 1

        # `hasActiveRun` is the authoritative answer and outranks the clock: a long tool
        # call can leave a session untouched well past the working window while the run is
        # very much alive.
        running = any(row.get("hasActiveRun") for row in rows)
        if running:
            state = "working"
        elif idle_seconds is not None and idle_seconds <= self.working_window_seconds:
            state = "working"
        else:
            state = "idle"

        return AgentActivityProbe(
            state=state,
            last_event_at=None if newest_ms is None else _iso_utc(newest_ms / 1000),
            idle_seconds=idle_seconds,
            sessions_total=max(sessions_total, len(rows)),
            sessions_active=len(active),
            agents=agents,
            latest=latest,
            recent_errors=self._recent_errors(active),
            log_errors=log_errors,
            error=None,
        )

    def _recent_errors(self, active: list[tuple[dict[str, Any], int]]) -> list[dict[str, Any]]:
        # `status` is the whole error surface of a session row: the gateway does not put
        # error text in the list, so there is deliberately no summary field here — an
        # unknown value is omitted, not faked.
        out: list[dict[str, Any]] = []
        for row, ms in active:
            status = (_as_text(row.get("status")) or "").lower()
            if status not in _FAILED_RUN_STATUSES:
                continue
            out.append(
                {
                    "at": _iso_utc(ms / 1000),
                    "agent_id": _row_agent_id(row),
                    "session_key": _row_session_key(row),
                    "type": status,
                }
            )
            if len(out) >= self.max_recent_errors:
                break
        return out

    def _read_log_errors(self) -> list[str]:
        """Tail the OpenClaw process log and return recent distinct error lines."""
        if self.log_file is None:
            return []
        try:
            with self.log_file.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                start = max(0, size - self.log_tail_bytes)
                handle.seek(start)
                raw = handle.read()
        except OSError:
            return []
        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\n")
        if start > 0 and lines:
            lines = lines[1:]  # drop leading partial line
        # Keep the most recent distinct error lines (scan newest-first, then restore order).
        seen: set[str] = set()
        picked: list[str] = []
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped or not any(token in stripped for token in _LOG_ERROR_TOKENS):
                continue
            truncated = _truncate(stripped)
            if truncated in seen:
                continue
            seen.add(truncated)
            picked.append(truncated)
            if len(picked) >= self.max_log_errors:
                break
        picked.reverse()
        return picked


def agent_activity_ping_payload(probe: AgentActivityProbe) -> dict[str, Any]:
    """Serialise an :class:`AgentActivityProbe` into the ping ``agent_activity`` object."""
    return {
        "state": probe.state,
        "last_event_at": probe.last_event_at,
        "idle_seconds": probe.idle_seconds,
        "sessions_total": probe.sessions_total,
        "sessions_active": probe.sessions_active,
        "agents": probe.agents,
        "latest": probe.latest,
        "recent_errors": probe.recent_errors,
        "log_errors": probe.log_errors,
        "error": probe.error,
    }


def _default_log_file() -> Path:
    """Path of the OpenClaw process log mirrored by ``openclaw_start`` (tee sink)."""
    explicit = (os.environ.get("OPENCLAW_LOG_FILE") or "").strip()
    if explicit:
        return Path(explicit)
    log_dir = (os.environ.get("OPENCLAW_LOG_DIR") or "").strip()
    if log_dir:
        return Path(log_dir) / "openclaw.log"
    state_dir = os.environ.get("OPENCLAW_STATE_DIR", _DEFAULT_STATE_DIR)
    return Path(state_dir) / "logs" / "openclaw.log"


def create_agent_activity_reader() -> AgentActivityReader:
    """Build a reader from environment (used by the ping loop)."""
    return AgentActivityReader(
        working_window_seconds=_env_float("AGENT_ACTIVITY_WORKING_WINDOW_SEC", 30.0),
        recent_window_seconds=_env_float("AGENT_ACTIVITY_RECENT_WINDOW_SEC", 300.0),
        max_recent_errors=_env_int("AGENT_ACTIVITY_MAX_ERRORS", 5),
        log_file=_default_log_file(),
        log_tail_bytes=_env_int("AGENT_ACTIVITY_LOG_TAIL_BYTES", 64 * 1024),
        max_log_errors=_env_int("AGENT_ACTIVITY_MAX_LOG_ERRORS", 5),
    )


def _rows_of(payload: Any) -> list[dict[str, Any]]:
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return []
    return [row for row in sessions if isinstance(row, dict)]


def _row_activity_ms(row: dict[str, Any]) -> int | None:
    """Newest timestamp on a session row, in epoch milliseconds.

    ``lastActivityAt`` moves on background work too, which is what "is anything happening"
    means here; ``updatedAt`` is the fallback for rows that predate it.
    """
    for key in ("lastActivityAt", "updatedAt"):
        value = _as_epoch_ms(row.get(key))
        if value is not None:
            return value
    return None


def _row_session_key(row: dict[str, Any]) -> str | None:
    """Session key of a ``sessions.list`` row (the row field is ``key``)."""
    return _as_text(row.get("key")) or _as_text(row.get("sessionKey"))


def _row_agent_id(row: dict[str, Any]) -> str:
    """Owning agent of a row.

    List rows carry no agent field of their own — the id is encoded in the session key
    (``agent:<agentId>:<rest>``), the same format ``parseAgentSessionKey`` reads upstream.
    Keys without that prefix (``global``, ``unknown``) have no owning agent.
    """
    explicit = _as_text(row.get("agentId"))
    if explicit:
        return explicit
    return agent_id_from_session_key(_row_session_key(row))


def _as_epoch_ms(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None


def _as_text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _iso_utc(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat()


def _truncate(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= _SUMMARY_LIMIT:
        return normalized
    return f"{normalized[: _SUMMARY_LIMIT - 1]}…"
