"""Mirror OpenClaw session activity into container stdout.

Chats, tool calls and run lifecycle all reach the gateway as events; ``sessions.subscribe``
asks for them across every session on one connection, which is what makes this a mirror of
the whole agent rather than of whichever sessions happened to exist at startup.

Each event becomes one ``[openclaw_session] …`` line. That prefix and the ``session=`` /
``type=`` keys are what the chat-analysis commands grep for, so they are part of the
contract with those tools, not incidental formatting.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, Final

from openclaw_diagnostics.gateway import (
    GatewayConnection,
    GatewayError,
    GatewayStream,
    agent_id_from_session_key,
)

TAG = "[openclaw_session]"
_SUMMARY_LIMIT = 240

# Event families a bare `sessions.subscribe` delivers for every session. Anything else on
# the socket (typing indicators, sharing changes, catalog notices) is UI bookkeeping that
# would only dilute the log.
_MIRRORED_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "session.message",
        "session.tool",
        "sessions.changed",
        "session.operation",
        "agent",
    }
)

_RECONNECT_DELAY_S: Final[float] = 2.0


async def monitor_session_logs(
    *,
    connection_factory: Callable[[], GatewayStream] = GatewayConnection,
    reconnect_delay_seconds: float = _RECONNECT_DELAY_S,
    max_events: int | None = None,
) -> None:
    """Stream session events to stdout until cancelled.

    Reconnects for as long as it runs: the gateway restarts on every agent restart and on
    every config apply, and a mirror that stopped at the first of those would be silent
    exactly when the logs matter most. Events emitted while disconnected are lost — the
    gateway keeps no backlog — which is the accepted cost of a debugging mirror.
    """
    emitted = 0
    reported_outage = False
    while True:
        try:
            async with connection_factory() as conn:
                await conn.call("sessions.subscribe", {})
                reported_outage = False
                async for frame in conn.events():
                    for line in session_log_lines(frame):
                        print(line, flush=True)
                        emitted += 1
                        if max_events is not None and emitted >= max_events:
                            return
        except GatewayError as exc:
            # One line per outage, not per retry: this loop runs alongside a booting gateway
            # and would otherwise fill the container log with the same line every few seconds.
            if not reported_outage:
                print(f"{TAG} agent=- session=- type=monitor.disconnected reason={exc}", flush=True)
                reported_outage = True
        await asyncio.sleep(reconnect_delay_seconds)


def session_log_lines(frame: dict[str, Any]) -> list[str]:
    """Render one gateway frame as stdout lines (empty when it is not a mirrored event)."""
    if frame.get("type") != "event":
        return []
    event = frame.get("event")
    if not isinstance(event, str) or event not in _MIRRORED_EVENTS:
        return []
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return [format_session_log_line(event=event, payload=payload)]


def format_session_log_line(*, event: str, payload: dict[str, Any]) -> str:
    """Format one gateway session event into a concise single-line stdout record."""
    raw_session = payload.get("session")
    session: dict[str, Any] = raw_session if isinstance(raw_session, dict) else {}
    # Top-level payloads say `sessionKey`; the embedded session-row snapshot says `key`.
    session_key = (
        _first_str(payload, "sessionKey")
        or _first_str(session, "sessionKey", "key")
        or _first_str(payload, "sessionId")
        or _first_str(session, "sessionId")
        or "unknown"
    )
    agent_id = _first_str(payload, "agentId") or _first_str(session, "agentId")
    if not agent_id:
        # Not every frame names the agent, but agent-scoped keys encode it.
        agent_id = agent_id_from_session_key(session_key)

    parts = [TAG, f"agent={agent_id}", f"session={session_key}", f"type={event}"]

    for key in _PROMOTED_KEYS:
        value = _promoted_scalar(payload, key)
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


#: Keys lifted into their own ``key=value`` token so they stay greppable in shipped logs.
#: The four run-lifecycle ones sit a level down, inside ``data``: before they were promoted they
#: reached the log only inside the truncated ``data=`` dump, so "how did this run end" survived
#: only when the JSON happened to fit under the limit — and the case that matters most, a run
#: that stopped at a tool call, is exactly the one whose payload is long.
_PROMOTED_KEYS: Final[tuple[str, ...]] = (
    "stream",
    "runId",
    "reason",
    "phase",
    "status",
    "stopReason",
    "operation",
    "aborted",
    "livenessState",
)


def _promoted_scalar(payload: dict[str, Any], key: str) -> Any:
    """First non-null value for ``key``, top level winning over the nested ``data`` block."""
    for source in (payload, payload.get("data")):
        if not isinstance(source, dict):
            continue
        value = source.get(key)
        if value is not None:
            return value
    return None


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_tool_name(payload: dict[str, Any]) -> str | None:
    for source in (payload, payload.get("data")):
        if not isinstance(source, dict):
            continue
        tool = source.get("tool")
        if isinstance(tool, str) and tool.strip():
            return tool.strip()
        if isinstance(tool, dict):
            name = tool.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        for key in ("toolName", "tool_name"):
            name = source.get(key)
            if isinstance(name, str) and name.strip():
                return name.strip()

    for item in _iter_content_items(payload):
        if item.get("type") != "toolCall":
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _extract_summary(payload: dict[str, Any]) -> str | None:
    for source in (payload, payload.get("message"), payload.get("data")):
        if not isinstance(source, dict):
            continue
        for key in ("text", "error", "result", "result_summary", "headline"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    command = _extract_command(payload)
    if command:
        return command

    message = payload.get("message")
    role = message.get("role") if isinstance(message, dict) else None
    text = _extract_content_text(payload)
    if isinstance(role, str) and role.strip() and text:
        return f"{role.strip()}: {text}"
    return text


def _extract_command(payload: dict[str, Any]) -> str | None:
    for source in (payload, payload.get("data")):
        if not isinstance(source, dict):
            continue
        for key in ("command", "raw_command", "input", "tool_input", "args"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = value.get("command")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()

        tool = source.get("tool")
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
    for source in (payload.get("message"), payload.get("data"), payload):
        if not isinstance(source, dict):
            continue
        content = source.get("content")
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
