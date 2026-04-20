from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class ManifestStorage:
    """Persist bundle manifest JSON under ``data_dir``."""

    _FILENAME = "manifest.json"

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    @property
    def manifest_path(self) -> Path:
        return self._data_dir / self._FILENAME

    @staticmethod
    def _compute_version(data: dict[str, Any]) -> str:
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def save(self, data: dict[str, Any]) -> tuple[Path, str]:
        """Write manifest as indented JSON (atomic via tmp + rename).

        Returns ``(path, version)`` where *version* is a short content hash.
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)
        version = self._compute_version(data)
        pretty = json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
        path = self.manifest_path
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(pretty, encoding="utf-8")
        os.replace(tmp_path, path)
        return path, version

    def load(self) -> dict[str, Any] | None:
        path = self.manifest_path
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("manifest.json root must be an object")
        return {str(k): v for k, v in raw.items()}

    def load_with_version(self) -> tuple[dict[str, Any], str] | None:
        data = self.load()
        if data is None:
            return None
        return data, self._compute_version(data)
