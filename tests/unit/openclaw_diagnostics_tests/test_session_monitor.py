from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self

import pytest
from openclaw_diagnostics.gateway import GatewayError
from openclaw_diagnostics.session_monitor import (
    TAG,
    format_session_log_line,
    monitor_session_logs,
    session_log_lines,
)

pytestmark = pytest.mark.unit


def _event(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "event", "event": name, "payload": payload}


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
