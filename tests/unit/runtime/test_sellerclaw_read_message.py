"""`sessions_history` truncates every message at 4000 characters and offers no way to ask for the
rest, so the tail of a long subagent report never reaches its requester. These tests cover the
command that reads the message whole from the local transcript instead."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_COMMAND = Path(__file__).resolve().parents[3] / "runtime" / "commands" / "sellerclaw_read_message"

SESSION_KEY = "agent:marketing:subagent:5247cb5c-2b3c-400c-ae49-e4cc748138f7"
SESSION_ID = "01J8XSESSION"


def _load_command() -> ModuleType:
    """The command ships as an extensionless script on PATH — import it by path."""
    spec = importlib.util.spec_from_loader(
        "sellerclaw_read_message",
        importlib.machinery.SourceFileLoader("sellerclaw_read_message", str(_COMMAND)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


read_message = _load_command()


def _event(role: str, text: str | None, *, blocks: list[dict[str, Any]] | None = None) -> str:
    content: Any = blocks if blocks is not None else text
    return json.dumps({"message": {"role": role, "content": content}})


def _make_transcript(
    root: Path,
    *,
    agent_id: str = "marketing",
    session_key: str = SESSION_KEY,
    events: list[tuple[int, str]] | None = None,
) -> Path:
    """Build a transcript database shaped like the one OpenClaw writes per agent."""
    path = root / "agents" / agent_id / "agent" / "openclaw-agent.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("create table session_nodes (session_key text, current_session_id text)")
        conn.execute("create table transcript_events (session_id text, seq integer, event_json text)")
        conn.execute(
            "insert into session_nodes (session_key, current_session_id) values (?, ?)",
            (session_key, SESSION_ID),
        )
        for seq, event_json in events or []:
            conn.execute(
                "insert into transcript_events (session_id, seq, event_json) values (?, ?, ?)",
                (SESSION_ID, seq, event_json),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def _read(root: Path, *, seq: int | None = None) -> str:
    path = read_message.transcript_path(SESSION_KEY, root=root)
    conn = read_message._connect(path)
    try:
        session_id = read_message.resolve_session_id(conn, SESSION_KEY)
        events = read_message.read_events(conn, session_id)
    finally:
        conn.close()
    _, event = read_message.pick(events, seq=seq)
    return read_message.message_text(event)


def test_a_report_longer_than_the_history_cap_comes_back_whole(tmp_path: Path) -> None:
    """The point of the command: 7.5 KB in, 7.5 KB out, conclusions included."""
    report = "## 7-Day Ad Performance Review\n" + ("filler line\n" * 600) + "\nRECOMMENDATIONS: fix it."
    assert len(report) > 4000, "the fixture must exceed the 4000-char history cap to be meaningful"
    _make_transcript(tmp_path, events=[(4, _event("user", "brief")), (37, _event("assistant", report))])

    assert _read(tmp_path) == report
    assert "RECOMMENDATIONS: fix it." in _read(tmp_path)


def test_the_newest_assistant_message_is_the_default(tmp_path: Path) -> None:
    """No `--seq`: the subagent's final report is what the requester is missing."""
    _make_transcript(
        tmp_path,
        events=[
            (5, _event("assistant", "Pulling the numbers.")),
            (6, _event("toolResult", "sellerclaw ad-accounts list")),
            (37, _event("assistant", "Total spend: $0.00")),
        ],
    )

    assert _read(tmp_path) == "Total spend: $0.00"


def test_an_explicit_seq_wins_over_the_default(tmp_path: Path) -> None:
    _make_transcript(
        tmp_path,
        events=[(5, _event("assistant", "Pulling the numbers.")), (37, _event("assistant", "Done."))],
    )

    assert _read(tmp_path, seq=5) == "Pulling the numbers."


def test_multi_block_content_is_joined(tmp_path: Path) -> None:
    """Assistant turns arrive as content blocks; the text of each belongs to the same message."""
    _make_transcript(
        tmp_path,
        events=[
            (
                37,
                _event(
                    "assistant",
                    None,
                    blocks=[
                        {"type": "text", "text": "Google: $0.00"},
                        {"type": "thinking", "thinking": "ignored — carries no text"},
                        {"type": "text", "text": "eBay: $0.00"},
                    ],
                ),
            )
        ],
    )

    assert _read(tmp_path) == "Google: $0.00\neBay: $0.00"


def test_listing_reports_every_event_with_its_size(tmp_path: Path) -> None:
    _make_transcript(
        tmp_path,
        events=[(4, _event("user", "brief")), (37, _event("assistant", "Total spend: $0.00"))],
    )
    path = read_message.transcript_path(SESSION_KEY, root=tmp_path)
    conn = read_message._connect(path)
    try:
        events = read_message.read_events(conn, read_message.resolve_session_id(conn, SESSION_KEY))
    finally:
        conn.close()

    listing = read_message.render_listing(events)

    assert "user" in listing and "assistant" in listing
    assert str(len("Total spend: $0.00")) in listing
    assert str(len("brief")) in listing


def test_the_live_database_is_opened_read_only(tmp_path: Path) -> None:
    """The owning agent may be mid-write; the reader must never be able to change its transcript."""
    path = _make_transcript(tmp_path, events=[(37, _event("assistant", "Done."))])
    conn = read_message._connect(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
            conn.execute("delete from transcript_events")
    finally:
        conn.close()


def test_a_corrupt_event_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One unparseable row must not cost the reader the message it came for."""
    _make_transcript(
        tmp_path,
        events=[(36, "{not json at all"), (37, _event("assistant", "Total spend: $0.00"))],
    )

    assert _read(tmp_path) == "Total spend: $0.00"


@pytest.mark.parametrize(
    ("session_key", "expected"),
    [
        pytest.param(SESSION_KEY, "marketing", id="subagent-session"),
        pytest.param("agent:supervisor:sellerclaw-ui:direct:09f7b1ce", "supervisor", id="chat-session"),
        pytest.param("agent:scout:subagent:2244d26d", "scout", id="another-specialist"),
    ],
)
def test_the_agent_owning_the_transcript_is_read_off_the_session_key(
    session_key: str, expected: str
) -> None:
    assert read_message.agent_id_from_session_key(session_key) == expected


@pytest.mark.parametrize(
    ("session_key", "reason"),
    [
        pytest.param("marketing", "not a session key", id="not-a-session-key"),
        pytest.param("agent:../../etc:subagent:x", "suspicious agent id", id="path-escape"),
        pytest.param("agent:a/b:subagent:x", "suspicious agent id", id="separator-in-id"),
    ],
)
def test_a_session_key_that_could_escape_the_state_directory_is_refused(
    session_key: str, reason: str
) -> None:
    """The agent id names a directory; a key that walks out of the state root is not read."""
    with pytest.raises(read_message.ReadFailed, match=reason):
        read_message.agent_id_from_session_key(session_key)


def test_an_unknown_session_says_so(tmp_path: Path) -> None:
    _make_transcript(tmp_path, events=[(37, _event("assistant", "Done."))])
    path = read_message.transcript_path(SESSION_KEY, root=tmp_path)
    conn = read_message._connect(path)
    try:
        with pytest.raises(read_message.ReadFailed, match="no session"):
            read_message.resolve_session_id(conn, "agent:marketing:subagent:does-not-exist")
    finally:
        conn.close()


def test_a_missing_transcript_names_the_agent_and_the_path(tmp_path: Path) -> None:
    with pytest.raises(read_message.ReadFailed, match="no transcript for agent 'marketing'"):
        read_message.transcript_path(SESSION_KEY, root=tmp_path)


def test_a_requested_seq_that_is_not_there_says_so(tmp_path: Path) -> None:
    _make_transcript(tmp_path, events=[(37, _event("assistant", "Done."))])

    with pytest.raises(read_message.ReadFailed, match="no message with seq 99"):
        _read(tmp_path, seq=99)


def test_a_session_without_any_assistant_text_says_so(tmp_path: Path) -> None:
    _make_transcript(tmp_path, events=[(4, _event("user", "brief")), (5, _event("assistant", "  "))])

    with pytest.raises(read_message.ReadFailed, match="no assistant message with text"):
        _read(tmp_path)


def test_the_state_root_follows_a_relocated_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container sets OPENCLAW_CONFIG_PATH; the state directory is its parent, not a guess."""
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", "/home/node/.openclaw/openclaw.json")

    assert read_message.state_root() == Path("/home/node/.openclaw")


def test_the_state_root_falls_back_to_the_stock_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.setenv("HOME", "/home/node")

    assert read_message.state_root() == Path("/home/node/.openclaw")
