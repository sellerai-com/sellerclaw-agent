"""Read OpenClaw session JSONL state to summarise agent activity for ping payloads.

OpenClaw is a third-party runtime we cannot modify, but it persists per-session
event logs under ``<state_dir>/agents/<agent_id>/sessions/<session_key>.jsonl``.
Reading those files (read-only) lets the edge agent attach a coarse "what is the
agent doing / is it stuck" signal to each ping, without any OpenClaw changes.

The JSONL schema is OpenClaw-internal and version-dependent, so every field is
parsed defensively: file mtime is the reliable liveness signal; event ``type`` /
tool / error markers are best-effort extractions that degrade to ``None``.

We also tail OpenClaw's stdout/stderr log (mirrored to a file by ``openclaw_start``;
see ``OPENCLAW_FILE_LOGS``) for error lines. Session JSONL only captures failures
*inside* a run — startup crashes, OOM, gateway/plugin errors that leave the agent
silent never reach a session file, but they do appear in the process log.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

_DEFAULT_STATE_DIR = "/home/node/.openclaw"
_SUMMARY_LIMIT = 240
# Error-ish markers we look for in scalar event fields.
_ERROR_TOKENS = frozenset({"error", "fatal", "failed", "failure", "deny", "denied", "exception"})
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
    """Snapshot of OpenClaw agent activity derived from session JSONL files."""

    state: str  # "working" | "idle" | "no_sessions" | "error"
    last_event_at: str | None  # ISO-8601 UTC of newest session-file mtime
    idle_seconds: float | None  # seconds since last_event_at
    sessions_total: int  # all session files on disk
    sessions_active: int  # session files touched within the recent window
    agents: dict[str, int]  # active session count per agent_id (subagents included)
    latest: dict[str, Any] | None  # last event of the most-recently-active session
    recent_errors: list[dict[str, Any]]  # error-ish events from active sessions
    log_errors: list[str]  # recent error lines from the OpenClaw process log (crashes/startup)
    error: str | None  # probe-level failure (e.g. unreadable state dir)


@dataclass
class AgentActivityReader:
    """Summarise OpenClaw session JSONL into an :class:`AgentActivityProbe`.

    Stateless across calls: every ``probe()`` re-scans the state dir, stats each
    session file, and tail-reads only the most-recently-active ones. Designed to
    run in the ping loop's executor (blocking file I/O) on each heartbeat.
    """

    state_dir: Path
    working_window_seconds: float = 30.0
    recent_window_seconds: float = 300.0
    tail_bytes: int = 32 * 1024
    max_active_files_parsed: int = 20
    max_recent_errors: int = 5
    log_file: Path | None = None
    log_tail_bytes: int = 64 * 1024
    max_log_errors: int = 5

    def probe(self) -> AgentActivityProbe:
        try:
            return self._probe()
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
                log_errors=self._read_log_errors(),
                error=str(exc)[:500],
            )

    def _probe(self) -> AgentActivityProbe:
        # Log errors are read independently of sessions: a crash that leaves no
        # session file is exactly the case we must still report.
        log_errors = self._read_log_errors()
        files = sorted(self.state_dir.glob("agents/*/sessions/*.jsonl"))
        stats: list[tuple[Path, float]] = []
        for path in files:
            try:
                stats.append((path, path.stat().st_mtime))
            except OSError:
                continue

        if not stats:
            return AgentActivityProbe(
                state="no_sessions",
                last_event_at=None,
                idle_seconds=None,
                sessions_total=0,
                sessions_active=0,
                agents={},
                latest=None,
                recent_errors=[],
                log_errors=log_errors,
                error=None,
            )

        now = time()
        # Newest first: liveness, "what's happening now", and errors all care about
        # the most-recently-touched sessions.
        stats.sort(key=lambda item: item[1], reverse=True)
        newest_path, newest_mtime = stats[0]
        idle_seconds = max(0.0, now - newest_mtime)

        active = [(path, mtime) for path, mtime in stats if now - mtime <= self.recent_window_seconds]
        agents: dict[str, int] = {}
        for path, _ in active:
            agent_id = _agent_id_for_path(path)
            agents[agent_id] = agents.get(agent_id, 0) + 1

        if idle_seconds <= self.working_window_seconds:
            state = "working"
        else:
            state = "idle"

        latest = self._latest_event(newest_path, newest_mtime, now)
        recent_errors = self._recent_errors(active, now)

        return AgentActivityProbe(
            state=state,
            last_event_at=_iso_utc(newest_mtime),
            idle_seconds=round(idle_seconds, 1),
            sessions_total=len(stats),
            sessions_active=len(active),
            agents=agents,
            latest=latest,
            recent_errors=recent_errors,
            log_errors=log_errors,
            error=None,
        )

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

    def _latest_event(self, path: Path, mtime: float, now: float) -> dict[str, Any] | None:
        events = self._read_tail_events(path)
        if not events:
            return None
        event = events[-1]
        return {
            "agent_id": _agent_id_for_path(path),
            "session_key": path.stem,
            "type": _event_type(event),
            "tool": _extract_tool_name(event),
            "age_seconds": round(max(0.0, now - mtime), 1),
        }

    def _recent_errors(self, active: list[tuple[Path, float]], now: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path, mtime in active[: self.max_active_files_parsed]:
            for event in self._read_tail_events(path):
                if not _looks_like_error(event):
                    continue
                out.append(
                    {
                        "at": _iso_utc(mtime),
                        "agent_id": _agent_id_for_path(path),
                        "session_key": path.stem,
                        "type": _event_type(event),
                        "summary": _extract_summary(event),
                    }
                )
                if len(out) >= self.max_recent_errors:
                    return out
        return out

    def _read_tail_events(self, path: Path) -> list[dict[str, Any]]:
        """Parse JSON objects from the last ``tail_bytes`` of a session file."""
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                start = max(0, size - self.tail_bytes)
                handle.seek(start)
                raw = handle.read()
        except OSError:
            return []
        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # Drop a leading partial line when we didn't start at byte 0.
        if start > 0 and lines:
            lines = lines[1:]
        events: list[dict[str, Any]] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events


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
        state_dir=Path(os.environ.get("OPENCLAW_STATE_DIR", _DEFAULT_STATE_DIR)),
        working_window_seconds=_env_float("AGENT_ACTIVITY_WORKING_WINDOW_SEC", 30.0),
        recent_window_seconds=_env_float("AGENT_ACTIVITY_RECENT_WINDOW_SEC", 300.0),
        tail_bytes=_env_int("AGENT_ACTIVITY_TAIL_BYTES", 32 * 1024),
        max_active_files_parsed=_env_int("AGENT_ACTIVITY_MAX_FILES", 20),
        max_recent_errors=_env_int("AGENT_ACTIVITY_MAX_ERRORS", 5),
        log_file=_default_log_file(),
        log_tail_bytes=_env_int("AGENT_ACTIVITY_LOG_TAIL_BYTES", 64 * 1024),
        max_log_errors=_env_int("AGENT_ACTIVITY_MAX_LOG_ERRORS", 5),
    )


def _iso_utc(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat()


def _agent_id_for_path(path: Path) -> str:
    parts = path.parts
    try:
        agents_idx = parts.index("agents")
    except ValueError:
        return "unknown"
    if agents_idx + 1 >= len(parts):
        return "unknown"
    return parts[agents_idx + 1]


def _event_type(payload: dict[str, Any]) -> str | None:
    for key in ("type", "event_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_tool_name(payload: dict[str, Any]) -> str | None:
    tool = payload.get("tool")
    if isinstance(tool, str) and tool.strip():
        return tool.strip()
    if isinstance(tool, dict):
        name = tool.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()

    tool_name = payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name.strip():
        return tool_name.strip()

    for item in _iter_content_items(payload):
        if item.get("type") != "toolCall":
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _extract_summary(payload: dict[str, Any]) -> str | None:
    for key in ("error", "text", "message", "result", "result_summary", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip())

    text = _extract_content_text(payload)
    return _truncate(text) if text else None


def _looks_like_error(payload: dict[str, Any]) -> bool:
    if payload.get("error"):
        return True
    for key in ("type", "event_type", "stage", "stopReason", "status", "decision", "level"):
        value = payload.get(key)
        if isinstance(value, str) and any(token in value.lower() for token in _ERROR_TOKENS):
            return True
    return False


def _extract_content_text(payload: dict[str, Any]) -> str | None:
    for item in _iter_content_items(payload):
        if item.get("type") in {"input_text", "output_text", "text"}:
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _iter_content_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return [item for item in content if isinstance(item, dict)]

    content = payload.get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def _truncate(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= _SUMMARY_LIMIT:
        return normalized
    return f"{normalized[: _SUMMARY_LIMIT - 1]}…"
