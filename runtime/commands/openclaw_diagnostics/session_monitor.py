"""Mirror OpenClaw session activity into container stdout.

Chats, tool calls and run lifecycle all reach the gateway as events; ``sessions.subscribe``
asks for them across every session on one connection, which is what makes this a mirror of
the whole agent rather than of whichever sessions happened to exist at startup.

Each event becomes one ``[openclaw_session] …`` line, except streamed reasoning and reply text,
which is logged once per block (see :class:`SessionLogMirror`). That prefix and the ``session=`` /
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

#: ``agent`` streams that arrive one frame per generated chunk, each carrying the whole text so far
#: (``data.text``) next to the new piece (``data.delta``). Printed per frame they repeat the same
#: truncated line hundreds of times per block, so the mirror logs each block once instead.
_CHUNKED_STREAMS: Final[frozenset[str]] = frozenset({"thinking", "assistant"})


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
        mirror = SessionLogMirror()
        try:
            async with connection_factory() as conn:
                await conn.call("sessions.subscribe", {})
                reported_outage = False
                async for frame in conn.events():
                    for line in mirror.feed(frame):
                        print(line, flush=True)
                        emitted += 1
                        if max_events is not None and emitted >= max_events:
                            return
        except GatewayError as exc:
            # A block still held when the socket drops would otherwise never be printed.
            for line in mirror.flush():
                print(line, flush=True)
            # One line per outage, not per retry: this loop runs alongside a booting gateway
            # and would otherwise fill the container log with the same line every few seconds.
            if not reported_outage:
                print(f"{TAG} agent=- session=- type=monitor.disconnected reason={exc}", flush=True)
                reported_outage = True
        await asyncio.sleep(reconnect_delay_seconds)


def session_log_lines(frame: dict[str, Any]) -> list[str]:
    """Render one gateway frame as stdout lines (empty when it is not a mirrored event)."""
    mirrored = _mirrored_event(frame)
    if mirrored is None:
        return []
    event, payload = mirrored
    return [format_session_log_line(event=event, payload=payload)]


class _HeldBlock:
    """The latest snapshot of one streamed text block, waiting for the block to end."""

    def __init__(self, *, run_id: str, stream: str, text: str, payload: dict[str, Any]) -> None:
        self.run_id = run_id
        self.stream = stream
        self.text = text
        self.payload = payload

    def continues_with(self, *, run_id: str, stream: str, text: str) -> bool:
        return run_id == self.run_id and stream == self.stream and text.startswith(self.text)

    def line(self) -> str:
        data = self.payload.get("data")
        data = {**data, "text": self.text} if isinstance(data, dict) else {"text": self.text}
        return format_session_log_line(event="agent", payload={**self.payload, "data": data})


class SessionLogMirror:
    """Gateway frames to stdout lines, with each streamed text block logged once.

    A chunk frame of a ``_CHUNKED_STREAMS`` stream is held as the latest snapshot of its block,
    one block per session. The held line is printed once the block is over — before the next frame
    of the same session, when a new block starts, or on :meth:`flush` — so it carries the block's
    leading text in full and keeps its place in the session's order. Every other frame is printed
    as it arrives.
    """

    def __init__(self) -> None:
        self._held: dict[str, _HeldBlock] = {}

    def feed(self, frame: dict[str, Any]) -> list[str]:
        mirrored = _mirrored_event(frame)
        if mirrored is None:
            return []
        event, payload = mirrored
        session_key = _session_key(payload)
        chunk = _chunk(event, payload)
        if chunk is None:
            return [*self._release(session_key), format_session_log_line(event=event, payload=payload)]

        run_id, stream, text, delta = chunk
        held = self._held.get(session_key)
        if text is None:
            # Some runtimes send the new piece alone; rebuild the running text from it.
            if not delta:
                return []  # a bare progress counter: nothing to show
            same_block = held is not None and held.run_id == run_id and held.stream == stream
            text = f"{held.text}{delta}" if held is not None and same_block else delta
        if held is not None and held.continues_with(run_id=run_id, stream=stream, text=text):
            held.text = text
            return []
        released = self._release(session_key)
        self._held[session_key] = _HeldBlock(run_id=run_id, stream=stream, text=text, payload=payload)
        return released

    def flush(self) -> list[str]:
        """Print every held block — for the end of the connection."""
        lines = [held.line() for held in self._held.values()]
        self._held.clear()
        return lines

    def _release(self, session_key: str) -> list[str]:
        held = self._held.pop(session_key, None)
        return [held.line()] if held is not None else []


def _mirrored_event(frame: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if frame.get("type") != "event":
        return None
    event = frame.get("event")
    if not isinstance(event, str) or event not in _MIRRORED_EVENTS:
        return None
    payload = frame.get("payload")
    return event, payload if isinstance(payload, dict) else {}


def _chunk(event: str, payload: dict[str, Any]) -> tuple[str, str, str | None, str | None] | None:
    """``(run_id, stream, text, delta)`` for a streamed-text chunk frame, else ``None``."""
    stream = payload.get("stream")
    data = payload.get("data")
    if event != "agent" or stream not in _CHUNKED_STREAMS or not isinstance(data, dict):
        return None
    if "delta" not in data and "progressTokens" not in data:
        return None
    text = data.get("text")
    delta = data.get("delta")
    return (
        str(payload.get("runId") or ""),
        str(stream),
        text if isinstance(text, str) else None,
        delta if isinstance(delta, str) else None,
    )


def _session_key(payload: dict[str, Any]) -> str:
    raw_session = payload.get("session")
    session: dict[str, Any] = raw_session if isinstance(raw_session, dict) else {}
    # Top-level payloads say `sessionKey`; the embedded session-row snapshot says `key`.
    return (
        _first_str(payload, "sessionKey")
        or _first_str(session, "sessionKey", "key")
        or _first_str(payload, "sessionId")
        or _first_str(session, "sessionId")
        or "unknown"
    )


def format_session_log_line(*, event: str, payload: dict[str, Any]) -> str:
    """Format one gateway session event into a concise single-line stdout record."""
    raw_session = payload.get("session")
    session: dict[str, Any] = raw_session if isinstance(raw_session, dict) else {}
    session_key = _session_key(payload)
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
