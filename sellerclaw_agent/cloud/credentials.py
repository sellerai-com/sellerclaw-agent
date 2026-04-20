from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class StoredCredentials:
    user_id: UUID
    user_email: str
    user_name: str
    access_token: str
    refresh_token: str
    connected_at: str


class CredentialsStorage:
    """Persist cloud auth credentials JSON under ``data_dir``."""

    _FILENAME = "credentials.json"

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
        access_token: str,
        refresh_token: str,
        connected_at: str,
    ) -> Path:
        """Write credentials as indented JSON (atomic via tmp + rename)."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "user_id": str(user_id),
            "user_email": user_email,
            "user_name": user_name,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "connected_at": connected_at,
        }
        pretty = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        path = self.credentials_path
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(pretty, encoding="utf-8")
        os.replace(tmp_path, path)
        return path

    def load(self) -> StoredCredentials | None:
        path = self.credentials_path
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("credentials.json root must be an object")
        data = {str(k): v for k, v in raw.items()}
        try:
            return StoredCredentials(
                user_id=UUID(str(data["user_id"])),
                user_email=str(data["user_email"]),
                user_name=str(data["user_name"]),
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                connected_at=str(data["connected_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def clear(self) -> None:
        path = self.credentials_path
        if path.is_file():
            path.unlink()

    def update_access_token(self, *, access_token: str) -> None:
        """Replace access token in stored credentials (after refresh)."""
        path = self.credentials_path
        if not path.is_file():
            raise FileNotFoundError(str(path))
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("credentials.json root must be an object")
        raw["access_token"] = access_token
        pretty = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(pretty, encoding="utf-8")
        os.replace(tmp_path, path)
