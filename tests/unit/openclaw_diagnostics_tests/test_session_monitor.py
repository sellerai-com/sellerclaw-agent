from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self

import pytest
from openclaw_diagnostics.gateway import GatewayError
from openclaw_diagnostics.session_monitor import (
    TAG,
    SessionLogMirror,
    format_session_log_line,
    monitor_session_logs,
    session_log_lines,
)

pytestmark = pytest.mark.unit


def _event(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "event", "event": name, "payload": payload}


def _chunk(
    session: str,
    text: str | None,
    *,
    delta: str | None = None,
    run: str = "run-1",
    stream: str = "thinking",
) -> dict[str, Any]:
    """One streamed-text frame as the gateway sends it: the whole text so far plus the new piece."""
    data: dict[str, Any] = {"delta": delta if delta is not None else (text or "")[-3:]}
    if text is not None:
        data["text"] = text
    return _event(
        "agent",
        {"sessionKey": session, "agentId": "supervisor", "runId": run, "stream": stream, "data": data},
    )


def _feed_all(mirror: SessionLogMirror, frames: list[dict[str, Any]]) -> list[str]:
    return [line for frame in frames for line in mirror.feed(frame)]


def _fields(line: str) -> dict[str, str]:
    """Parse the ``key=value`` tail of a log line, stopping at the free-form field."""
    out: dict[str, str] = {}
    for token in line.removeprefix(f"{TAG} ").split(" "):
        key, sep, value = token.partition("=")
        if sep and key not in out:
            out[key] = value
        if key in {"summary", "data"}:
            break
    return out


class _FakeGateway:
    def __init__(
        self,
        frames: list[dict[str, Any]],
        *,
        connect_error: str | None = None,
    ) -> None:
        self._frames = frames
        self._connect_error = connect_error
        self.subscribed = False

    async def __aenter__(self) -> Self:
        if self._connect_error:
            raise GatewayError(self._connect_error)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        assert method == "sessions.subscribe"
        self.subscribed = True
        return {"subscribed": True}

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        for frame in self._frames:
            yield frame
        raise GatewayError("connection lost")


def test_message_event_carries_identity_and_text() -> None:
    line = format_session_log_line(
        event="session.message",
        payload={
            "sessionKey": "agent:supervisor:sellerclaw-ui:direct:c1",
            "agentId": "supervisor",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Готово."}]},
        },
    )

    fields = _fields(line)
    assert line.startswith(TAG)
    assert fields["agent"] == "supervisor"
    assert fields["session"] == "agent:supervisor:sellerclaw-ui:direct:c1"
    assert fields["type"] == "session.message"
    assert "summary=assistant: Готово." in line


def test_tool_event_names_the_tool_and_run() -> None:
    line = format_session_log_line(
        event="session.tool",
        payload={
            "sessionKey": "s1",
            "agentId": "sellercart",
            "runId": "run-7",
            "stream": "tool",
            "data": {"toolName": "exec", "command": "sellerclaw sellercart status"},
        },
    )

    fields = _fields(line)
    assert fields["type"] == "session.tool"
    assert fields["runId"] == "run-7"
    assert fields["stream"] == "tool"
    assert fields["tool"] == "exec"
    assert "summary=sellerclaw sellercart status" in line


def test_identity_falls_back_to_the_embedded_session_row() -> None:
    """`session.tool` frames may carry identity only in the row snapshot, whose key field
    is ``key`` and which names no agent — the id is parsed out of the session key."""
    line = format_session_log_line(
        event="session.tool",
        payload={"session": {"key": "agent:shopify:subagent:1"}},
    )

    fields = _fields(line)
    assert fields["agent"] == "shopify"
    assert fields["session"] == "agent:shopify:subagent:1"


def test_unknown_identity_is_marked_rather_than_dropped() -> None:
    fields = _fields(format_session_log_line(event="agent", payload={}))

    assert fields["agent"] == "unknown"
    assert fields["session"] == "unknown"


def test_event_without_summary_falls_back_to_compact_payload() -> None:
    line = format_session_log_line(
        event="sessions.changed",
        payload={"sessionKey": "s1", "agentId": "supervisor", "reason": "lifecycle"},
    )

    assert _fields(line)["reason"] == "lifecycle"
    assert '"sessionKey": "s1"' in line or '"sessionKey":"s1"' in line


def test_long_summary_is_truncated() -> None:
    line = format_session_log_line(
        event="session.message",
        payload={"sessionKey": "s1", "message": {"role": "user", "text": "x" * 900}},
    )

    assert line.endswith("…")
    assert len(line) < 500


def test_run_end_promotes_how_the_run_finished() -> None:
    """A lifecycle frame keeps ``phase``/``stopReason`` one level down, inside ``data``.

    Shape taken from a real announce run (staging chat c7ca1f27): the report reached the owner
    and the run then ended at the ``message`` tool call rather than taking another turn. That
    verdict used to survive only inside the truncated ``data=`` dump, which is why it could not
    be counted across runs.
    """
    line = format_session_log_line(
        event="agent",
        payload={
            "sessionKey": "agent:supervisor:sellerclaw-ui:direct:c7ca1f27",
            "agentId": "supervisor",
            "stream": "lifecycle",
            "runId": "announce:v1:agent:scout:subagent:2244d26d",
            "data": {
                "aborted": False,
                "livenessState": "working",
                "phase": "end",
                "stopReason": "toolUse",
                "startedAt": 1788336975554,
            },
        },
    )

    fields = _fields(line)
    assert fields["phase"] == "end"
    assert fields["stopReason"] == "toolUse"
    assert fields["aborted"] == "false"
    assert fields["livenessState"] == "working"
    assert fields["runId"] == "announce:v1:agent:scout:subagent:2244d26d"


def test_a_top_level_key_wins_over_the_same_key_nested() -> None:
    line = format_session_log_line(
        event="session.tool",
        payload={"sessionKey": "s1", "status": "running", "data": {"status": "queued"}},
    )

    assert _fields(line)["status"] == "running"


def test_only_mirrored_event_families_produce_lines() -> None:
    assert session_log_lines(_event("session.message", {"sessionKey": "s1"}))
    assert session_log_lines(_event("agent", {"sessionKey": "s1"}))
    # UI bookkeeping we deliberately keep out of the log.
    assert session_log_lines(_event("session.typing", {"sessionKey": "s1"})) == []
    assert session_log_lines(_event("sessions.catalog.host", {})) == []


def test_non_event_frames_are_ignored() -> None:
    assert session_log_lines({"type": "res", "id": "1", "ok": True, "payload": {}}) == []


def test_malformed_payload_still_produces_a_line() -> None:
    assert session_log_lines({"type": "event", "event": "agent", "payload": "not-a-dict"})


@pytest.mark.parametrize("stream", [pytest.param("thinking", id="reasoning"), pytest.param("assistant", id="reply")])
def test_a_streamed_block_is_one_line_with_its_full_text(stream: str) -> None:
    """Every chunk frame carries the text so far; one line per chunk printed the same prefix
    hundreds of times per block. The block now lands once, before the event that follows it."""
    mirror = SessionLogMirror()

    lines = _feed_all(
        mirror,
        [
            _chunk("s1", "I'm", stream=stream),
            _chunk("s1", "I'm on a", stream=stream),
            _chunk("s1", "I'm on a background run", stream=stream),
            _event("session.tool", {"sessionKey": "s1", "agentId": "supervisor", "data": {"toolName": "exec"}}),
        ],
    )

    assert [_fields(line)["type"] for line in lines] == ["agent", "session.tool"]
    assert _fields(lines[0])["stream"] == stream
    assert lines[0].endswith("summary=I'm on a background run")


def test_a_new_block_releases_the_previous_one() -> None:
    mirror = SessionLogMirror()

    lines = _feed_all(
        mirror,
        [
            _chunk("s1", "First thought"),
            _chunk("s1", "Second"),  # does not continue the first text: a new block
            _chunk("s1", "Second thought"),
            _chunk("s1", "Reply", run="run-1", stream="assistant"),
        ],
    )

    assert [line.rsplit("summary=", 1)[1] for line in lines] == ["First thought", "Second thought"]


def test_blocks_of_different_sessions_do_not_release_each_other() -> None:
    mirror = SessionLogMirror()

    interleaved = _feed_all(
        mirror,
        [
            _chunk("sup", "Checking"),
            _chunk("sub", "Searching"),
            _chunk("sup", "Checking the task"),
            _chunk("sub", "Searching CJ"),
            _event("session.message", {"sessionKey": "sub", "message": {"role": "assistant", "text": "Done"}}),
        ],
    )

    assert [(_fields(line)["session"], _fields(line)["type"]) for line in interleaved] == [
        ("sub", "agent"),
        ("sub", "session.message"),
    ]
    assert interleaved[0].endswith("summary=Searching CJ")
    assert [line.rsplit("summary=", 1)[1] for line in mirror.flush()] == ["Checking the task"]
    assert mirror.flush() == []


@pytest.mark.parametrize(
    ("frames", "expected"),
    [
        pytest.param(
            [_chunk("s1", None, delta="Look"), _chunk("s1", None, delta="ing up")],
            ["Looking up"],
            id="delta-only chunks are joined",
        ),
        pytest.param(
            [_event("agent", {"sessionKey": "s1", "runId": "r", "stream": "thinking", "data": {"progressTokens": 40}})],
            [],
            id="bare progress counter is dropped",
        ),
    ],
)
def test_chunks_without_a_running_text(frames: list[dict[str, Any]], expected: list[str]) -> None:
    mirror = SessionLogMirror()

    assert _feed_all(mirror, frames) == []
    assert [line.rsplit("summary=", 1)[1] for line in mirror.flush()] == expected


def test_non_chunk_agent_frames_are_printed_at_once() -> None:
    """Lifecycle, item and tool frames of the same run are not held — only streamed text is."""
    mirror = SessionLogMirror()

    lines = mirror.feed(
        _event("agent", {"sessionKey": "s1", "runId": "r", "stream": "lifecycle", "data": {"phase": "end"}})
    )

    assert len(lines) == 1
    assert _fields(lines[0])["phase"] == "end"


async def test_monitor_prints_a_held_block_when_the_connection_drops(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gateways = [
        _FakeGateway([_chunk("s1", "Half"), _chunk("s1", "Half a thought")]),
        _FakeGateway([_event("session.message", {"sessionKey": "s1"})]),
    ]

    await monitor_session_logs(
        connection_factory=lambda: gateways.pop(0),
        reconnect_delay_seconds=0,
        max_events=1,
    )

    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith(TAG)]
    assert lines[0].endswith("summary=Half a thought")
    assert _fields(lines[-1])["type"] == "session.message"


async def test_monitor_subscribes_then_mirrors_events(capsys: pytest.CaptureFixture[str]) -> None:
    gateway = _FakeGateway(
        [
            _event("session.message", {"sessionKey": "s1", "agentId": "supervisor"}),
            _event("session.typing", {"sessionKey": "s1"}),
            _event("session.tool", {"sessionKey": "s1", "agentId": "supervisor"}),
        ]
    )

    await monitor_session_logs(connection_factory=lambda: gateway, max_events=2)

    assert gateway.subscribed
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith(TAG)]
    assert [_fields(line)["type"] for line in lines] == ["session.message", "session.tool"]


async def test_monitor_reports_a_dropped_connection_and_reconnects(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gateway restarts on every agent restart; a silent mirror would hide that."""
    gateways = [
        _FakeGateway([], connect_error="gateway unreachable"),
        _FakeGateway([_event("agent", {"sessionKey": "s1", "agentId": "supervisor"})]),
    ]

    await monitor_session_logs(
        connection_factory=lambda: gateways.pop(0),
        reconnect_delay_seconds=0,
        max_events=1,
    )

    out = capsys.readouterr().out
    assert "type=monitor.disconnected" in out
    assert "type=agent" in out
    assert not gateways  # both connections were used, so the reconnect happened


async def test_monitor_reports_one_line_per_outage_not_per_retry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The mirror starts alongside a booting gateway; repeating the line would drown the log."""
    gateways = [
        _FakeGateway([], connect_error="gateway unreachable"),
        _FakeGateway([], connect_error="gateway unreachable"),
        _FakeGateway([], connect_error="gateway unreachable"),
        _FakeGateway([_event("agent", {"sessionKey": "s1"})]),
    ]

    await monitor_session_logs(
        connection_factory=lambda: gateways.pop(0),
        reconnect_delay_seconds=0,
        max_events=1,
    )

    out = capsys.readouterr().out
    assert out.count("type=monitor.disconnected") == 1
    assert "type=agent" in out
