from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from sellerclaw_agent.cloud.credentials import CredentialsStorage, StoredAgentCredentials

pytestmark = pytest.mark.unit


def test_save_load_roundtrip(tmp_path: Path) -> None:
    storage = CredentialsStorage(tmp_path)
    uid = UUID("35922ddf-4020-5179-b163-3d90bcb86b00")
    path = storage.save(
        user_id=uid,
        user_email="a@b.c",
        user_name="Alice",
        agent_token="sca_test",
        connected_at="2026-04-14T12:00:00Z",
    )
    assert path == tmp_path / "agent_token.json"
    loaded = storage.load()
    assert loaded == StoredAgentCredentials(
        user_id=uid,
        user_email="a@b.c",
        user_name="Alice",
        agent_token="sca_test",
        connected_at="2026-04-14T12:00:00Z",
    )


def test_load_missing_returns_none(tmp_path: Path) -> None:
    storage = CredentialsStorage(tmp_path)
    assert storage.load() is None


def test_clear_removes_file(tmp_path: Path) -> None:
    storage = CredentialsStorage(tmp_path)
    storage.save(
        user_id=UUID("35922ddf-4020-5179-b163-3d90bcb86b00"),
        user_email="x@y.z",
        user_name="",
        agent_token="sca_x",
        connected_at="t",
    )
    assert storage.credentials_path.is_file()
    storage.clear()
    assert not storage.credentials_path.is_file()
    storage.clear()


def test_load_invalid_json_raises(tmp_path: Path) -> None:
    storage = CredentialsStorage(tmp_path)
    storage.credentials_path.write_text("not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        storage.load()


def test_load_not_object_raises(tmp_path: Path) -> None:
    storage = CredentialsStorage(tmp_path)
    storage.credentials_path.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="agent_token.json root must be an object"):
        storage.load()


def test_load_incomplete_object_returns_none(tmp_path: Path) -> None:
    storage = CredentialsStorage(tmp_path)
    storage.credentials_path.write_text(
        '{"user_id": "35922ddf-4020-5179-b163-3d90bcb86b00"}',
        encoding="utf-8",
    )
    assert storage.load() is None


def test_load_invalid_user_id_returns_none(tmp_path: Path) -> None:
    storage = CredentialsStorage(tmp_path)
    storage.credentials_path.write_text(
        '{"user_id": "not-a-uuid", "user_email": "a@b.c", "user_name": "", '
        '"agent_token": "sca_x", "connected_at": "t"}',
        encoding="utf-8",
    )
    assert storage.load() is None
