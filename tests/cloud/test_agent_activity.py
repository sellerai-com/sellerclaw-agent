from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sellerclaw_agent.cloud.agent_activity import (
    AgentActivityReader,
    agent_activity_ping_payload,
    create_agent_activity_reader,
)

pytestmark = pytest.mark.unit


def _session_file(state_dir: Path, *, agent_id: str, session_key: str) -> Path:
    path = state_dir / "agents" / agent_id / "sessions" / f"{session_key}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _reader(state_dir: Path, **kwargs: object) -> AgentActivityReader:
    return AgentActivityReader(state_dir=state_dir, **kwargs)  # type: ignore[arg-type]


def test_no_sessions_reports_no_sessions_state(tmp_path: Path) -> None:
    probe = _reader(tmp_path / ".openclaw").probe()

    assert probe.state == "no_sessions"
    assert probe.sessions_total == 0
    assert probe.sessions_active == 0
    assert probe.agents == {}
    assert probe.latest is None
    assert probe.recent_errors == []
    assert probe.error is None


def test_recent_activity_reports_working_with_latest_event(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session = _session_file(state_dir, agent_id="supervisor", session_key="s1")
    session.write_text(
        '{"type":"assistant_message","text":"hi"}\n'
        '{"type":"tool_call","tool":"exec"}\n',
        encoding="utf-8",
    )

    probe = _reader(state_dir).probe()

    assert probe.state == "working"
    assert probe.sessions_total == 1
    assert probe.sessions_active == 1
    assert probe.agents == {"supervisor": 1}
    assert probe.latest is not None
    assert probe.latest["agent_id"] == "supervisor"
    assert probe.latest["session_key"] == "s1"
    assert probe.latest["type"] == "tool_call"
    assert probe.latest["tool"] == "exec"
    assert probe.last_event_at is not None
    assert probe.idle_seconds is not None and probe.idle_seconds >= 0.0


def test_idle_when_last_event_outside_working_window(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session = _session_file(state_dir, agent_id="scout", session_key="s1")
    session.write_text('{"type":"message","text":"x"}\n', encoding="utf-8")
    # Push mtime well past the working window but inside the recent window.
    old = time.time() - 120
    os.utime(session, (old, old))

    probe = _reader(state_dir, working_window_seconds=30.0, recent_window_seconds=300.0).probe()

    assert probe.state == "idle"
    assert probe.sessions_active == 1  # still within recent window
    assert probe.idle_seconds is not None and probe.idle_seconds >= 120.0


def test_subagents_counted_per_agent_id(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    for agent_id in ("supervisor", "scout", "shopify"):
        _session_file(state_dir, agent_id=agent_id, session_key="s1").write_text(
            '{"type":"message","text":"x"}\n', encoding="utf-8"
        )

    probe = _reader(state_dir).probe()

    assert probe.sessions_total == 3
    assert probe.agents == {"supervisor": 1, "scout": 1, "shopify": 1}


def test_recent_errors_extracted_from_session_tail(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session = _session_file(state_dir, agent_id="supplier", session_key="s1")
    session.write_text(
        '{"type":"tool_call","tool":"exec"}\n'
        '{"type":"tool_error","error":"connect timeout to shopify"}\n',
        encoding="utf-8",
    )

    probe = _reader(state_dir).probe()

    assert len(probe.recent_errors) == 1
    err = probe.recent_errors[0]
    assert err["agent_id"] == "supplier"
    assert err["type"] == "tool_error"
    assert err["summary"] == "connect timeout to shopify"
    assert err["at"] is not None


def test_recent_errors_capped(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session = _session_file(state_dir, agent_id="scout", session_key="s1")
    lines = "".join(f'{{"type":"error","error":"boom {i}"}}\n' for i in range(10))
    session.write_text(lines, encoding="utf-8")

    probe = _reader(state_dir, max_recent_errors=3).probe()

    assert len(probe.recent_errors) == 3


def test_stale_session_outside_recent_window_not_active(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session = _session_file(state_dir, agent_id="scout", session_key="old")
    session.write_text('{"type":"message","text":"x"}\n', encoding="utf-8")
    old = time.time() - 10_000
    os.utime(session, (old, old))

    probe = _reader(state_dir, recent_window_seconds=300.0).probe()

    assert probe.state == "idle"
    assert probe.sessions_total == 1
    assert probe.sessions_active == 0
    assert probe.agents == {}


def test_malformed_jsonl_does_not_crash(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session = _session_file(state_dir, agent_id="scout", session_key="s1")
    session.write_bytes(b'not-json\n{"type":"message","text":"ok"}\n\xff\xff\n')

    probe = _reader(state_dir).probe()

    assert probe.error is None
    assert probe.state == "working"
    assert probe.latest is not None


def test_ping_payload_serialises_all_fields(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    _session_file(state_dir, agent_id="supervisor", session_key="s1").write_text(
        '{"type":"tool_call","tool":"exec"}\n', encoding="utf-8"
    )

    payload = agent_activity_ping_payload(_reader(state_dir).probe())

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


def test_log_errors_extracted_from_process_log(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    state_dir.mkdir(parents=True)
    log_file = tmp_path / "openclaw.log"
    log_file.write_text(
        "[openclaw] gateway starting\n"
        "info: bound to loopback:7789\n"
        "FATAL: gateway failed to bind: EADDRINUSE\n"
        "visited https://shop.example/error-page (not an error)\n"
        "Error: plugin sellerclaw-ui failed to load\n",
        encoding="utf-8",
    )
    probe = _reader(state_dir, log_file=log_file).probe()

    assert probe.state == "no_sessions"  # crash before any session file
    assert probe.log_errors == [
        "FATAL: gateway failed to bind: EADDRINUSE",
        "Error: plugin sellerclaw-ui failed to load",
    ]


def test_log_errors_absent_when_no_log_file(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    _session_file(state_dir, agent_id="supervisor", session_key="s1").write_text(
        '{"type":"message","text":"x"}\n', encoding="utf-8"
    )
    probe = _reader(state_dir, log_file=tmp_path / "missing.log").probe()
    assert probe.log_errors == []


def test_log_errors_capped_and_deduped(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    state_dir.mkdir(parents=True)
    log_file = tmp_path / "openclaw.log"
    lines = ["ERROR repeated boom\n"] * 4 + [f"ERROR unique {i}\n" for i in range(10)]
    log_file.write_text("".join(lines), encoding="utf-8")
    probe = _reader(state_dir, log_file=log_file, max_log_errors=3).probe()

    assert len(probe.log_errors) == 3
    assert len(set(probe.log_errors)) == 3  # deduped


def test_log_errors_present_alongside_session_activity(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    _session_file(state_dir, agent_id="supervisor", session_key="s1").write_text(
        '{"type":"tool_call","tool":"exec"}\n', encoding="utf-8"
    )
    log_file = tmp_path / "openclaw.log"
    log_file.write_text("ERROR something broke in the gateway\n", encoding="utf-8")
    probe = _reader(state_dir, log_file=log_file).probe()

    assert probe.state == "working"
    assert probe.log_errors == ["ERROR something broke in the gateway"]


def test_log_errors_capture_silent_delivery_failures(tmp_path: Path) -> None:
    """Completion-wake failures carry no ERROR/FATAL token but must still surface.

    These leave the agent looking healthy (session ends, no session-level error)
    while the result never reaches the user — the class of bug the probe exists for.
    """
    state_dir = tmp_path / ".openclaw"
    state_dir.mkdir(parents=True)
    log_file = tmp_path / "openclaw.log"
    log_file.write_text(
        "08:10:30 [tools/media-generate-background-shared] "
        "Media generation completion wake failed; requester session was not woken\n"
        "[warn] Subagent announce give up (retry-limit): "
        "active requester session could not be woken\n"
        "info: serving on loopback:7789\n",
        encoding="utf-8",
    )

    probe = _reader(state_dir, log_file=log_file).probe()

    assert probe.log_errors == [
        "08:10:30 [tools/media-generate-background-shared] "
        "Media generation completion wake failed; requester session was not woken",
        "[warn] Subagent announce give up (retry-limit): "
        "active requester session could not be woken",
    ]


def test_log_errors_capture_undelivered_completion_answers(tmp_path: Path) -> None:
    """The plugin's own silent-delivery marker rides along; routine delivery lines do not.

    ``sellerclaw-ui`` logs every completion delivery under one prefix, but only the lines that
    cost the owner an answer carry ``undelivered`` — without that split the ping's error list
    would fill up with successful sends and stop meaning anything.
    """
    state_dir = tmp_path / ".openclaw"
    state_dir.mkdir(parents=True)
    log_file = tmp_path / "openclaw.log"
    log_file.write_text(
        "sellerclaw-ui[delivery] substituted completion answer for runtime fallback "
        "session_key=agent:supervisor:sellerclaw-ui:direct:48729abf\n"
        "sellerclaw-ui[delivery] undelivered: completion run produced no answer "
        "session_key=agent:supervisor:sellerclaw-ui:direct:48729abf\n",
        encoding="utf-8",
    )

    probe = _reader(state_dir, log_file=log_file).probe()

    assert probe.log_errors == [
        "sellerclaw-ui[delivery] undelivered: completion run produced no answer "
        "session_key=agent:supervisor:sellerclaw-ui:direct:48729abf",
    ]


def test_create_reader_honours_state_dir_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", "/custom/state")
    monkeypatch.setenv("AGENT_ACTIVITY_WORKING_WINDOW_SEC", "5")
    reader = create_agent_activity_reader()

    assert reader.state_dir == Path("/custom/state")
    assert reader.working_window_seconds == 5.0
