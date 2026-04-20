"""Minimal Server-Sent Events line decoder for httpx streams."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class _AsyncLineSource(Protocol):
    def aiter_lines(self) -> AsyncIterator[str]:
        ...


async def iter_sse_events(response: _AsyncLineSource) -> AsyncIterator[tuple[str, str]]:
    """Yield ``(event_name, data)`` for each SSE frame (``data`` may be multi-line joined)."""
    event_name = "message"
    data_lines: list[str] = []
    async for raw in response.aiter_lines():
        line = raw.strip("\r")
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
        elif line == "":
            if data_lines:
                yield event_name, "\n".join(data_lines)
            data_lines = []
            event_name = "message"
    if data_lines:
        yield event_name, "\n".join(data_lines)
