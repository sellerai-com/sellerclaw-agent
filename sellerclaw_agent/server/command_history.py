from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_JSON = json.JSONDecoder()


def _dumps_line(data: dict[str, Any]) -> str:
    """Serialize a single entry as one compact JSONL line (no embedded newlines)."""
    return json.dumps(data, ensure_ascii=False, default=str)


def _parse_json_dicts(text: str) -> list[dict[str, Any]]:
    """Parse consecutive JSON objects from ``text``, skipping malformed segments.

    Primarily parses one-object-per-line JSONL written by :meth:`CommandHistoryStorage.append`,
    but tolerates multi-line / pretty-printed values left by earlier versions of this module.
    When a segment fails to decode, we resync to the next ``{`` rather than discarding the
    rest of the file — otherwise a single partial write would orphan all newer entries.
    """
    n = len(text)
    idx = 0
    out: list[dict[str, Any]] = []
    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = _JSON.raw_decode(text, idx)
        except ValueError:
            next_brace = text.find("{", idx + 1)
            if next_brace == -1:
                _log.warning(
                    "command_history_trailing_garbage_skipped bytes=%d", n - idx
                )
                break
            _log.warning(
                "command_history_malformed_segment_skipped bytes=%d", next_brace - idx
            )
            idx = next_brace
            continue
        idx = end
        if isinstance(obj, dict):
            out.append({str(k): v for k, v in obj.items()})
    return out


class CommandHistoryStorage:
    """Persist received edge commands as a bounded JSONL ring buffer.

    File format: one JSON object per line (JSONL). This keeps ``tail -f``, ``jq -c``,
    ``grep`` and log collectors predictable, while the loader still accepts legacy
    pretty-printed files for forward compatibility.
    """

    _FILENAME = "command_history.jsonl"

    def __init__(self, data_dir: Path, max_entries: int = 200) -> None:
        self._data_dir = data_dir
        self._max_entries = max_entries

    @property
    def history_path(self) -> Path:
        return self._data_dir / self._FILENAME

    def append(self, entry: dict[str, Any]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self.history_path
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        existing: list[dict[str, Any]] = _parse_json_dicts(text)
        existing.append(entry)

        if len(existing) > self._max_entries:
            existing = existing[-self._max_entries :]

        out = "\n".join(_dumps_line(e) for e in existing) + "\n"
        tmp_path = path.with_suffix(".jsonl.tmp")
        tmp_path.write_text(out, encoding="utf-8")
        os.replace(tmp_path, path)

    def load(self) -> list[dict[str, Any]]:
        """Return entries newest-first. Malformed segments are skipped with a log warning."""
        path = self.history_path
        if not path.is_file():
            return []
        entries = _parse_json_dicts(path.read_text(encoding="utf-8"))
        entries.reverse()
        return entries
