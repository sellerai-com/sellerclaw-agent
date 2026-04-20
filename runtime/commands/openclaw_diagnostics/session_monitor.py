from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAG = "[openclaw_session]"
DEFAULT_STATE_DIR = Path("/home/node/.openclaw")
_SUMMARY_LIMIT = 240


@dataclass
class SessionFileTracker:
    """Track incremental reads for one OpenClaw session JSONL file."""

    position: int
    inode: int
    remainder: str = ""


def list_session_files(*, state_dir: Path) -> list[Path]:
    """Return all agent session JSONL files under the OpenClaw state directory."""
    return sorted(state_dir.glob("agents/*/sessions/*.jsonl"))


def seed_existing_session_offsets(*, state_dir: Path) -> dict[Path, SessionFileTracker]:
    """Initialize trackers at EOF so old sessions are not replayed on startup."""
    trackers: dict[Path, SessionFileTracker] = {}
    for path in list_session_files(state_dir=state_dir):
        try:
            stat = path.stat()
        except OSError:
            continue
        trackers[path] = SessionFileTracker(position=stat.st_size, inode=stat.st_ino)
    return trackers


def collect_new_session_log_lines(
    *,
    state_dir: Path,
    trackers: dict[Path, SessionFileTracker],
) -> list[str]:
    """Read newly appended session JSONL lines and return formatted stdout log lines."""
    lines: list[str] = []
    current_files = list_session_files(state_dir=state_dir)
    active_paths = set(current_files)

    for stale_path in tuple(trackers):
        if stale_path not in active_paths:
            trackers.pop(stale_path, None)

    for path in current_files:
        try:
            stat = path.stat()
        except OSError:
            continue

        tracker = trackers.get(path)
        if tracker is None:
            tracker = SessionFileTracker(position=0, inode=stat.st_ino)
            trackers[path] = tracker
        elif tracker.inode != stat.st_ino or stat.st_size < tracker.position:
            tracker.position = 0
            tracker.inode = stat.st_ino
            tracker.remainder = ""

        if stat.st_size == tracker.position:
            continue

        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(tracker.position)
                chunk = handle.read()
                tracker.position = handle.tell()
        except OSError:
            continue

        if not chunk:
            continue

        buffered = tracker.remainder + chunk
        raw_lines = buffered.splitlines(keepends=True)
        tracker.remainder = ""
        if raw_lines and not raw_lines[-1].endswith(("\n", "\r")):
            tracker.remainder = raw_lines.pop()

        for raw_line in raw_lines:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            lines.append(format_session_log_line(path=path, raw_line=line))

    return lines


def monitor_session_logs(
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    interval_seconds: float = 1.0,
    max_scans: int | None = None,
) -> None:
    """Continuously mirror all agent session events into container stdout."""
    trackers = seed_existing_session_offsets(state_dir=state_dir)
    scans = 0
    while True:
        for line in collect_new_session_log_lines(state_dir=state_dir, trackers=trackers):
            print(line, flush=True)

        scans += 1
        if max_scans is not None and scans >= max_scans:
            return
        time.sleep(interval_seconds)


def format_session_log_line(*, path: Path, raw_line: str) -> str:
    """Format one JSONL session event into a concise single-line stdout record."""
    agent_id = _agent_id_for_path(path)
    session_key = path.stem

    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError:
        return f"{TAG} agent={agent_id} session={session_key} raw={_truncate(raw_line)}"

    if not isinstance(payload, dict):
        return f"{TAG} agent={agent_id} session={session_key} raw={_truncate(raw_line)}"

    parts = [
        TAG,
        f"agent={agent_id}",
        f"session={session_key}",
    ]

    timestamp = _first_str(payload, "timestamp", "created_at", "time")
    if timestamp:
        parts.append(f"ts={timestamp}")

    event_type = _first_str(payload, "type", "event_type")
    if event_type:
        parts.append(f"type={event_type}")

    for key in ("runId", "stage", "decision", "reason", "stopReason", "status"):
        value = payload.get(key)
        if value is not None:
            parts.append(f"{key}={_display_scalar(value)}")

    tool_name = _extract_tool_name(payload)
    if tool_name:
        parts.append(f"tool={tool_name}")

    summary = _extract_summary(payload)
    if summary:
        parts.append(f"summary={_truncate(summary)}")
    else:
        parts.append(f"data={_truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True))}")

    return " ".join(parts)


def _agent_id_for_path(path: Path) -> str:
    parts = path.parts
    try:
        agents_idx = parts.index("agents")
    except ValueError:
        return "unknown"
    if agents_idx + 1 >= len(parts):
        return "unknown"
    return parts[agents_idx + 1]


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
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
    for key in ("text", "message", "result", "result_summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    command = _extract_command(payload)
    if command:
        return command

    role = payload.get("role")
    text = _extract_content_text(payload)
    if isinstance(role, str) and role.strip() and text:
        return f"{role.strip()}: {text}"
    return text


def _extract_command(payload: dict[str, Any]) -> str | None:
    for key in ("command", "raw_command", "input", "tool_input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("command")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()

    tool = payload.get("tool")
    if isinstance(tool, dict):
        tool_input = tool.get("input")
        if isinstance(tool_input, str) and tool_input.strip():
            return tool_input.strip()
        if isinstance(tool_input, dict):
            nested = tool_input.get("command")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def _extract_content_text(payload: dict[str, Any]) -> str | None:
    for item in _iter_content_items(payload):
        item_type = item.get("type")
        if item_type in {"input_text", "output_text", "text"}:
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


def _display_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
