from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class StoredAgentCredentials:
    """Persisted edge agent cloud auth (``sca_…`` token only, no user JWT)."""

    user_id: UUID
    user_email: str
    user_name: str
    agent_token: str
    connected_at: str


class CredentialsStorage:
    """Persist agent cloud credentials as JSON under ``data_dir`` (``agent_token.json``)."""

    _FILENAME = "agent_token.json"

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    @property
    def credentials_path(self) -> Path:
        return self._data_dir / self._FILENAME

    def save(
        self,
        *,
        user_id: UUID,
        user_email: str,
        user_name: str,
        agent_token: str,
        connected_at: str,
    ) -> Path:
        """Write credentials as indented JSON (atomic via tmp + rename)."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "user_id": str(user_id),
            "user_email": user_email,
            "user_name": user_name,
            "agent_token": agent_token,
            "connected_at": connected_at,
        }
        pretty = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        path = self.credentials_path
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(pretty, encoding="utf-8")
        os.replace(tmp_path, path)
        # Lazy import to avoid import cycle (cli_config is a consumer of this layer).
        from sellerclaw_agent.cloud.cli_config import write_cli_config

        write_cli_config(token=agent_token)
        return path

    def load(self) -> StoredAgentCredentials | None:
        path = self.credentials_path
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("agent_token.json root must be an object")
        data = {str(k): v for k, v in raw.items()}
        try:
            return StoredAgentCredentials(
                user_id=UUID(str(data["user_id"])),
                user_email=str(data["user_email"]),
                user_name=str(data["user_name"]),
                agent_token=str(data["agent_token"]),
                connected_at=str(data["connected_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def clear(self) -> None:
        path = self.credentials_path
        if path.is_file():
            path.unlink()
        from sellerclaw_agent.cloud.cli_config import remove_cli_config

        remove_cli_config()
