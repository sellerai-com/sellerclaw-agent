from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pytest
from sellerclaw_agent.cloud.agent_activity import (
    AgentActivityReader,
    agent_activity_ping_payload,
)
from openclaw_diagnostics.gateway import GatewayError, GatewayUnreachableError

pytestmark = pytest.mark.unit

_NOW_MS = 1_770_000_000_000


class _FakeGateway:
    """Stands in for a gateway connection, answering the two RPCs the probe makes."""

    def __init__(
        self,
        *,
        sessions: dict[str, Any] | None = None,
        audit: dict[str, Any] | None = None,
        connect_unreachable: str | None = None,
        sessions_error: str | None = None,
        audit_error: str | None = None,
    ) -> None:
        self._sessions = sessions if sessions is not None else {"sessions": [], "totalCount": 0}
        self._audit = audit if audit is not None else {"events": []}
        self._connect_unreachable = connect_unreachable
        self._sessions_error = sessions_error
        self._audit_error = audit_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> Self:
        if self._connect_unreachable:
            raise GatewayUnreachableError(self._connect_unreachable)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params or {}))
        if method == "sessions.list":
            if self._sessions_error:
                raise GatewayError(self._sessions_error)
            return self._sessions
        if method == "audit.list":
            if self._audit_error:
                raise GatewayError(self._audit_error)
            return self._audit
        raise AssertionError(f"unexpected RPC: {method}")


def _reader(gateway: _FakeGateway, **kwargs: Any) -> AgentActivityReader:
    return AgentActivityReader(connection_factory=lambda: gateway, **kwargs)


def _row(**overrides: Any) -> dict[str, Any]:
    """A ``sessions.list`` row as the gateway really shapes it.

    The session key field is ``key`` and there is no agent field — the owning agent is
    encoded in the key (``agent:<agentId>:<rest>``).
    """
    row = {
        "key": "agent:supervisor:sellerclaw-ui:direct:c1",
        "updatedAt": _NOW_MS,
        "lastActivityAt": _NOW_MS,
        "status": "done",
        "hasActiveRun": False,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sellerclaw_agent.cloud.agent_activity.time", lambda: _NOW_MS / 1000)


async def test_no_sessions_when_gateway_reports_none() -> None:
    probe = await _reader(_FakeGateway()).probe()

    assert probe.state == "no_sessions"
    assert probe.sessions_total == 0
    assert probe.sessions_active == 0
    assert probe.last_event_at is None
    assert probe.error is None


async def test_active_run_reports_working_even_when_session_row_is_stale() -> None:
    """A long tool call leaves the row untouched; the run is still in flight."""
    stale_ms = _NOW_MS - 10 * 60 * 1000
    gateway = _FakeGateway(
        sessions={
            "sessions": [_row(updatedAt=stale_ms, lastActivityAt=stale_ms, hasActiveRun=True)],
            "totalCount": 1,
        }
    )

    probe = await _reader(gateway).probe()

    assert probe.state == "working"
    assert probe.idle_seconds == 600.0
    # Ten minutes is outside the recent window, so it counts as a session but not an active one.
    assert probe.sessions_total == 1
    assert probe.sessions_active == 0


async def test_recent_activity_without_active_run_reports_working() -> None:
    gateway = _FakeGateway(
        sessions={"sessions": [_row(lastActivityAt=_NOW_MS - 5_000)], "totalCount": 1}
    )

    probe = await _reader(gateway).probe()

    assert probe.state == "working"
    assert probe.idle_seconds == 5.0
    assert probe.sessions_active == 1
    assert probe.agents == {"supervisor": 1}


async def test_quiet_session_reports_idle() -> None:
    gateway = _FakeGateway(
        sessions={"sessions": [_row(lastActivityAt=_NOW_MS - 120_000)], "totalCount": 1}
    )

    probe = await _reader(gateway).probe()

    assert probe.state == "idle"
    assert probe.idle_seconds == 120.0
    assert probe.sessions_active == 1  # still inside the 300s recent window


async def test_subagent_sessions_counted_per_agent_from_the_session_key() -> None:
    gateway = _FakeGateway(
        sessions={
            "sessions": [
                _row(key="agent:supervisor:main"),
                _row(key="agent:sellercart:subagent:1"),
                _row(key="agent:sellercart:subagent:2"),
                # Keys without the agent prefix have no owning agent.
                _row(key="global"),
            ],
            "totalCount": 4,
        }
    )

    probe = await _reader(gateway).probe()

    assert probe.agents == {"supervisor": 1, "sellercart": 2, "unknown": 1}
    assert probe.sessions_active == 4


async def test_sessions_total_prefers_server_count_over_returned_rows() -> None:
    """The row list is capped; the server's own total is what the operator should see."""
    gateway = _FakeGateway(sessions={"sessions": [_row()], "totalCount": 137})

    probe = await _reader(gateway).probe()

    assert probe.sessions_total == 137
    assert probe.sessions_active == 1


async def test_failed_runs_surface_as_recent_errors() -> None:
    gateway = _FakeGateway(
        sessions={
            "sessions": [
                _row(key="agent:supervisor:main"),
                _row(key="agent:sellercart:subagent:1", status="failed"),
                _row(key="agent:sellercart:subagent:2", status="killed"),
                _row(key="agent:supervisor:cron:x", status="timeout"),
            ],
            "totalCount": 4,
        }
    )

    probe = await _reader(gateway).probe()

    assert [error["session_key"] for error in probe.recent_errors] == [
        "agent:sellercart:subagent:1",
        "agent:sellercart:subagent:2",
        "agent:supervisor:cron:x",
    ]
    assert [error["type"] for error in probe.recent_errors] == ["failed", "killed", "timeout"]
    assert probe.recent_errors[0]["agent_id"] == "sellercart"
    # The gateway has no error text in the session list — the field is omitted, not faked.
    assert all("summary" not in error for error in probe.recent_errors)


async def test_recent_errors_capped() -> None:
    rows = [_row(key=f"agent:supervisor:s{i}", status="failed") for i in range(10)]
    gateway = _FakeGateway(sessions={"sessions": rows, "totalCount": 10})

    probe = await _reader(gateway, max_recent_errors=3).probe()

    assert len(probe.recent_errors) == 3


async def test_latest_event_comes_from_the_audit_ledger() -> None:
    gateway = _FakeGateway(
        sessions={"sessions": [_row()], "totalCount": 1},
        audit={
            "events": [
                {
                    "kind": "tool_action",
                    "action": "tool.action.finished",
                    "status": "succeeded",
                    "agentId": "sellercart",
                    "sessionKey": "agent:sellercart:subagent:1",
                    "toolName": "exec",
                    "occurredAt": _NOW_MS - 2_000,
                }
            ]
        },
    )

    probe = await _reader(gateway).probe()

    assert probe.latest == {
        "agent_id": "sellercart",
        "session_key": "agent:sellercart:subagent:1",
        "type": "tool.action.finished",
        "tool": "exec",
        "age_seconds": 2.0,
    }


async def test_latest_event_is_optional_when_audit_recording_is_off() -> None:
    """The ledger is best-effort by design; losing it must not cost us the probe."""
    gateway = _FakeGateway(
        sessions={"sessions": [_row()], "totalCount": 1},
        audit_error="audit.list failed: UNAVAILABLE: audit disabled",
    )

    probe = await _reader(gateway).probe()

    assert probe.latest is None
    assert probe.state == "working"
    assert probe.error is None


async def test_unreachable_gateway_reports_idle_with_reason() -> None:
    """Runs only exist inside the gateway, so no gateway means nothing is running."""
    gateway = _FakeGateway(connect_unreachable="gateway unreachable at ws://127.0.0.1:7789: refused")

    probe = await _reader(gateway).probe()

    assert probe.state == "idle"
    assert probe.error is not None
    assert probe.error.startswith("gateway_unreachable:")
    assert probe.idle_seconds is None
    assert probe.sessions_total == 0


async def test_gateway_that_answers_and_refuses_is_reported_distinctly() -> None:
    """A stopped agent and a gateway rejecting our scope must not read the same."""
    gateway = _FakeGateway(sessions_error="sessions.list failed: FORBIDDEN: missing scope")

    probe = await _reader(gateway).probe()

    assert probe.state == "idle"
    assert probe.error is not None
    assert probe.error.startswith("gateway_error:")
    assert "FORBIDDEN" in probe.error


async def test_unexpected_failure_reports_error_state() -> None:
    class _Broken(_FakeGateway):
        async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
            raise ValueError("boom")

    probe = await _reader(_Broken()).probe()

    assert probe.state == "error"
    assert probe.error == "boom"


async def test_ping_payload_shape() -> None:
    gateway = _FakeGateway(sessions={"sessions": [_row()], "totalCount": 1})

    payload = agent_activity_ping_payload(await _reader(gateway).probe())

    assert set(payload) == {
        "state",
        "last_event_at",
        "idle_seconds",
        "sessions_total",
        "sessions_active",
        "agents",
        "latest",
        "recent_errors",
        "log_errors",
        "error",
    }
    assert payload["state"] == "working"


async def test_log_errors_extracted_from_process_log(tmp_path: Path) -> None:
    log_file = tmp_path / "openclaw.log"
    log_file.write_text(
        "[openclaw] gateway starting\n"
        "info: bound to loopback:7789\n"
        "FATAL: gateway failed to bind: EADDRINUSE\n"
        "visited https://shop.example/error-page (not an error)\n"
        "Error: plugin sellerclaw-ui failed to load\n",
        encoding="utf-8",
    )
    # A gateway that died on startup has no sessions to report, but the crash is in the log.
    gateway = _FakeGateway(connect_unreachable="gateway unreachable")

    probe = await _reader(gateway, log_file=log_file).probe()

    assert probe.log_errors == [
        "FATAL: gateway failed to bind: EADDRINUSE",
        "Error: plugin sellerclaw-ui failed to load",
    ]


async def test_log_errors_absent_when_no_log_file(tmp_path: Path) -> None:
    gateway = _FakeGateway(sessions={"sessions": [_row()], "totalCount": 1})

    probe = await _reader(gateway, log_file=tmp_path / "missing.log").probe()

    assert probe.log_errors == []


async def test_log_errors_capped_and_deduped(tmp_path: Path) -> None:
    log_file = tmp_path / "openclaw.log"
    lines = ["ERROR repeated boom\n"] * 4 + [f"ERROR unique {i}\n" for i in range(10)]
    log_file.write_text("".join(lines), encoding="utf-8")

    probe = await _reader(_FakeGateway(), log_file=log_file, max_log_errors=3).probe()

    assert len(probe.log_errors) == 3
    assert len(set(probe.log_errors)) == 3  # deduped


async def test_log_errors_present_alongside_session_activity(tmp_path: Path) -> None:
    log_file = tmp_path / "openclaw.log"
    log_file.write_text("ERROR something broke\n", encoding="utf-8")
    gateway = _FakeGateway(sessions={"sessions": [_row()], "totalCount": 1})

    probe = await _reader(gateway, log_file=log_file).probe()

    assert probe.state == "working"
    assert probe.log_errors == ["ERROR something broke"]


async def test_probe_sends_only_schema_valid_list_params() -> None:
    """The params schema is closed (additionalProperties: false) — extras reject the call."""
    gateway = _FakeGateway(sessions={"sessions": [_row()], "totalCount": 1})

    await _reader(gateway).probe()

    method, params = gateway.calls[0]
    assert method == "sessions.list"
    assert params == {"limit": 200}
