from __future__ import annotations

import pytest
from sellerclaw_agent.cloud.sse_codec import iter_sse_events

pytestmark = pytest.mark.unit


class _LinesResponse:
    """Minimal async line source mimicking ``httpx.Response.aiter_lines``."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_iter_sse_events_parses_event_and_data() -> None:
    raw = [
        "event: user_message",
        'data: {"a":1}',
        "",
        "event: heartbeat",
        "data: {}",
        "",
    ]
    res = _LinesResponse(raw)
    out = [(n, d) async for n, d in iter_sse_events(res)]
    assert out == [("user_message", '{"a":1}'), ("heartbeat", "{}")]


@pytest.mark.asyncio
async def test_iter_sse_multiline_data() -> None:
    res = _LinesResponse(
        [
            "event: x",
            "data: line1",
            "data: line2",
            "",
        ],
    )
    out = [(n, d) async for n, d in iter_sse_events(res)]
    assert out == [("x", "line1\nline2")]
