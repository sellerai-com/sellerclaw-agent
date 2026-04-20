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
