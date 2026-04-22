from __future__ import annotations

import pytest
from sellerclaw_agent.server.command_history import CommandHistoryStorage

pytestmark = pytest.mark.unit


def _entry(n: int) -> dict[str, object]:
    return {
        "command_id": f"cmd-{n}",
        "command_type": "restart",
        "issued_at": "2026-04-15T12:00:00+00:00",
        "received_at": "2026-04-15T12:00:01+00:00",
        "executed_at": "2026-04-15T12:00:02+00:00",
        "outcome": "ok",
        "error": None,
    }


def test_append_and_load_returns_newest_first(tmp_path) -> None:
    storage = CommandHistoryStorage(tmp_path)
    storage.append(_entry(1))
    storage.append(_entry(2))
    storage.append(_entry(3))

    loaded = storage.load()
    assert [e["command_id"] for e in loaded] == ["cmd-3", "cmd-2", "cmd-1"]
    assert loaded[0]["command_type"] == "restart"
    assert loaded[0]["outcome"] == "ok"


def test_load_missing_file_returns_empty(tmp_path) -> None:
    assert CommandHistoryStorage(tmp_path).load() == []


def test_ring_buffer_truncates_oldest(tmp_path) -> None:
    storage = CommandHistoryStorage(tmp_path, max_entries=3)
    for i in range(5):
        storage.append(_entry(i))

    loaded = storage.load()
    assert [e["command_id"] for e in loaded] == ["cmd-4", "cmd-3", "cmd-2"]


def test_malformed_lines_are_skipped(tmp_path) -> None:
    storage = CommandHistoryStorage(tmp_path)
    storage.append(_entry(1))
    path = storage.history_path
    path.write_text(
        path.read_text(encoding="utf-8") + "not-json\n{\"bad\":\n",
        encoding="utf-8",
    )

    loaded = storage.load()
    assert len(loaded) == 1
    assert loaded[0]["command_id"] == "cmd-1"


def test_append_creates_data_dir(tmp_path) -> None:
    target = tmp_path / "nested" / "data"
    storage = CommandHistoryStorage(target)
    storage.append(_entry(1))
    assert (target / "command_history.jsonl").is_file()


def test_append_writes_one_object_per_line(tmp_path) -> None:
    storage = CommandHistoryStorage(tmp_path)
    storage.append(_entry(1))
    storage.append(_entry(2))

    lines = storage.history_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    import json as _json
    parsed = [_json.loads(line) for line in lines]
    assert [p["command_id"] for p in parsed] == ["cmd-1", "cmd-2"]


def test_load_accepts_legacy_pretty_printed_file(tmp_path) -> None:
    """Older agent versions wrote indented JSON — readers must still parse it."""
    import json as _json

    pretty = (
        _json.dumps(_entry(1), indent=2, ensure_ascii=False)
        + "\n"
        + _json.dumps(_entry(2), indent=2, ensure_ascii=False)
        + "\n"
    )
    storage = CommandHistoryStorage(tmp_path)
    storage.history_path.write_text(pretty, encoding="utf-8")

    loaded = storage.load()

    assert [e["command_id"] for e in loaded] == ["cmd-2", "cmd-1"]


def test_load_resyncs_after_malformed_segment_between_valid_entries(tmp_path) -> None:
    """A partial/corrupt middle segment must not orphan valid newer entries."""
    import json as _json

    line1 = _json.dumps(_entry(1), ensure_ascii=False)
    line3 = _json.dumps(_entry(3), ensure_ascii=False)
    content = f"{line1}\n{{\"bad\":\n{line3}\n"
    storage = CommandHistoryStorage(tmp_path)
    storage.history_path.write_text(content, encoding="utf-8")

    loaded = storage.load()

    ids = [e["command_id"] for e in loaded]
    assert "cmd-1" in ids and "cmd-3" in ids
