from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class CommandHistoryStorage:
    """Persist received edge commands as a bounded JSONL ring buffer."""

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
        line = json.dumps(entry, ensure_ascii=False, default=str)

        existing: list[str] = []
        if path.is_file():
            existing = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        existing.append(line)

        if len(existing) > self._max_entries:
            existing = existing[-self._max_entries :]

        tmp_path = path.with_suffix(".jsonl.tmp")
        tmp_path.write_text("\n".join(existing) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)

    def load(self) -> list[dict[str, Any]]:
        """Return entries newest-first. Malformed lines are silently skipped."""
        path = self.history_path
        if not path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                entries.append({str(k): v for k, v in parsed.items()})
        entries.reverse()
        return entries
