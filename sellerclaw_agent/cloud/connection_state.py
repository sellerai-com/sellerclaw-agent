from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class StoredEdgeSession:
    agent_instance_id: UUID
    protocol_version: int


class EdgeSessionStorage:
    """Persist edge session id returned by ``POST /agent/connection/connect``."""

    _FILENAME = "edge_session.json"

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    @property
    def path(self) -> Path:
        return self._data_dir / self._FILENAME

    def save(self, *, agent_instance_id: UUID, protocol_version: int) -> Path:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "agent_instance_id": str(agent_instance_id),
            "protocol_version": int(protocol_version),
        }
        pretty = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        p = self.path
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(pretty, encoding="utf-8")
        os.replace(tmp, p)
        return p

    def load(self) -> StoredEdgeSession | None:
        p = self.path
        if not p.is_file():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        try:
            return StoredEdgeSession(
                agent_instance_id=UUID(str(raw["agent_instance_id"])),
                protocol_version=int(raw["protocol_version"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def clear(self) -> None:
        p = self.path
        if p.is_file():
            p.unlink()
