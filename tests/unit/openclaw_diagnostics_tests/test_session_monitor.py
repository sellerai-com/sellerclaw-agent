from __future__ import annotations

from pathlib import Path

import pytest
from openclaw_diagnostics.__main__ import main
from openclaw_diagnostics.session_monitor import (
    collect_new_session_log_lines,
    format_session_log_line,
    monitor_session_logs,
    seed_existing_session_offsets,
)

pytestmark = pytest.mark.unit


def _session_file(state_dir: Path, *, agent_id: str, session_key: str) -> Path:
    path = state_dir / "agents" / agent_id / "sessions" / f"{session_key}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_seed_existing_session_offsets_skips_old_history(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-a")
    session_file.write_text('{"type":"message","text":"old line"}\n', encoding="utf-8")

    trackers = seed_existing_session_offsets(state_dir=state_dir)

    assert collect_new_session_log_lines(state_dir=state_dir, trackers=trackers) == []


def test_collect_new_session_log_lines_emits_new_file_events(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    trackers = seed_existing_session_offsets(state_dir=state_dir)
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-b")
    session_file.write_text('{"type":"assistant_message","text":"hello from scout"}\n', encoding="utf-8")

    lines = collect_new_session_log_lines(state_dir=state_dir, trackers=trackers)

    assert len(lines) == 1
    assert "agent=scout" in lines[0]
    assert "session=session-b" in lines[0]
    assert "type=assistant_message" in lines[0]
    assert "summary=hello from scout" in lines[0]


def test_collect_new_session_log_lines_emits_appended_lines_only(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="supplier", session_key="session-c")
    session_file.write_text('{"type":"message","text":"first"}\n', encoding="utf-8")
    trackers = seed_existing_session_offsets(state_dir=state_dir)

    with session_file.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"tool_call","tool":"exec","command":"curl https://example.test"}\n')

    lines = collect_new_session_log_lines(state_dir=state_dir, trackers=trackers)

    assert len(lines) == 1
    assert "agent=supplier" in lines[0]
    assert "tool=exec" in lines[0]
    assert "summary=curl https://example.test" in lines[0]


def test_format_session_log_line_falls_back_to_raw_for_invalid_json(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="shopify", session_key="session-d")

    line = format_session_log_line(path=session_file, raw_line="not-json")

    assert "agent=shopify" in line
    assert "session=session-d" in line
    assert "raw=not-json" in line


@pytest.mark.parametrize(
    ("raw_line", "must_contain"),
    [
        pytest.param(
            '{"type":"tool_call","tool":"exec","command":"curl https://x.test"}',
            ["type=tool_call", "tool=exec", "summary=curl https://x.test"],
            id="tool-with-command",
        ),
        pytest.param(
            '{"type":"msg","message":{"content":[{"type":"output_text","text":"hello"}]}}',
            ["type=msg", "summary=hello"],
            id="nested-content-text",
        ),
        pytest.param(
            '{"type":"x","tool":{"name":"browser","input":{"command":"open"}}}',
            ["tool=browser", "summary=open"],
            id="nested-tool-input-command",
        ),
        pytest.param(
            "[]",
            ["raw="],
            id="non-dict-json",
        ),
        pytest.param(
            "{}",
            ["[openclaw_session]", "agent=", "session="],
            id="empty-dict-fallback-data",
        ),
        pytest.param(
            '{"message":"plain text summary"}',
            ["summary=plain text summary"],
            id="message-string-summary",
        ),
        pytest.param(
            '{"message":{"content":[{"type":"output_text","text":"nested"}]}}',
            ["summary=nested"],
            id="message-dict-content-extraction",
        ),
    ],
)
def test_format_session_log_line_payload_variants(
    tmp_path: Path,
    raw_line: str,
    must_contain: list[str],
) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-fmt")

    line = format_session_log_line(path=session_file, raw_line=raw_line)

    for fragment in must_contain:
        assert fragment in line


def test_format_session_log_line_truncates_long_summary(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-long")
    long_text = "word " * 80
    raw = '{"type":"message","text":"' + long_text + '"}'

    line = format_session_log_line(path=session_file, raw_line=raw)

    assert "summary=" in line
    assert line.endswith("…")
    assert len(line) < len(raw)


def test_collect_new_session_log_lines_resets_on_inode_change(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-inode")
    session_file.write_text('{"type":"message","text":"old"}\n', encoding="utf-8")
    trackers = seed_existing_session_offsets(state_dir=state_dir)

    # Rename away then create a new file at the same path so inode always changes
    # (unlink+create can reuse inode on Linux and leave EOF offset mid-file).
    session_file.rename(session_file.with_suffix(".jsonl.bak"))
    session_file.write_text('{"type":"message","text":"after-rotate"}\n', encoding="utf-8")

    lines = collect_new_session_log_lines(state_dir=state_dir, trackers=trackers)

    assert len(lines) == 1
    assert "after-rotate" in lines[0]


def test_collect_new_session_log_lines_resets_on_truncation_without_inode_change(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-trunc")
    session_file.write_text(
        '{"type":"message","text":"long long long content here"}\n',
        encoding="utf-8",
    )
    trackers = seed_existing_session_offsets(state_dir=state_dir)

    session_file.write_text('{"type":"message","text":"short"}\n', encoding="utf-8")

    lines = collect_new_session_log_lines(state_dir=state_dir, trackers=trackers)

    assert len(lines) == 1
    assert "short" in lines[0]


def test_collect_new_session_log_lines_buffers_incomplete_final_line(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-partial")
    session_file.write_text('{"type":"message","text":"first"}\n', encoding="utf-8")
    trackers = seed_existing_session_offsets(state_dir=state_dir)

    with session_file.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"message","text":"second')

    assert collect_new_session_log_lines(state_dir=state_dir, trackers=trackers) == []

    with session_file.open("a", encoding="utf-8") as handle:
        handle.write(' part"}\n')

    lines = collect_new_session_log_lines(state_dir=state_dir, trackers=trackers)

    assert len(lines) == 1
    assert "second part" in lines[0]


def test_collect_new_session_log_lines_drop_tracker_when_file_removed(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-gone")
    session_file.write_text('{"type":"message","text":"x"}\n', encoding="utf-8")
    trackers = seed_existing_session_offsets(state_dir=state_dir)

    session_file.unlink()

    assert collect_new_session_log_lines(state_dir=state_dir, trackers=trackers) == []
    assert session_file not in trackers


def test_collect_new_session_log_lines_reads_file_with_invalid_utf8_bytes(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    session_file = _session_file(state_dir, agent_id="scout", session_key="session-bad")
    session_file.write_bytes(b'{"type":"message","text":"ok"}\n\xff\xff\n')
    trackers: dict = {}

    lines = collect_new_session_log_lines(state_dir=state_dir, trackers=trackers)

    assert len(lines) >= 1
    assert "ok" in lines[0]


def test_monitor_session_logs_prints_mirrored_lines(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".openclaw"
    state_dir.mkdir(parents=True)

    def fake_collect(**kwargs: object) -> list[str]:
        return ["[openclaw_session] agent=scout session=x type=test"]

    monkeypatch.setattr(
        "openclaw_diagnostics.session_monitor.collect_new_session_log_lines",
        fake_collect,
    )
    monkeypatch.setattr(
        "openclaw_diagnostics.session_monitor.seed_existing_session_offsets",
        lambda **_: {},
    )

    monitor_session_logs(state_dir=state_dir, interval_seconds=0.0, max_scans=1)

    out = capsys.readouterr().out
    assert "[openclaw_session]" in out


def test_main_monitor_sessions_exits_after_max_scans(tmp_path: Path) -> None:
    state_dir = tmp_path / ".openclaw"
    state_dir.mkdir(parents=True)

    code = main(["monitor-sessions", "--state-dir", str(state_dir), "--interval", "0", "--max-scans", "1"])

    assert code == 0
