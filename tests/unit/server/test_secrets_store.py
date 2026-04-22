from __future__ import annotations

import json
from pathlib import Path

import pytest

from sellerclaw_agent.server.secrets_store import (
    load_or_create_secrets,
    migrate_legacy_manifest_tokens_into_secrets,
    reset_secrets_cache,
)

pytestmark = pytest.mark.unit


def test_load_prefers_env_per_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_secrets_cache()
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "env-local")
    monkeypatch.setenv("SELLERCLAW_GATEWAY_TOKEN", "env-gw")
    monkeypatch.setenv("SELLERCLAW_HOOKS_TOKEN", "env-hooks")
    sec = load_or_create_secrets(tmp_path)
    assert sec.local_api_key == "env-local"
    assert sec.gateway_token == "env-gw"
    assert sec.hooks_token == "env-hooks"


def test_idempotent_and_mode_600(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_secrets_cache()
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    monkeypatch.delenv("SELLERCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SELLERCLAW_HOOKS_TOKEN", raising=False)
    a = load_or_create_secrets(tmp_path)
    b = load_or_create_secrets(tmp_path)
    assert a == b
    path = tmp_path / "secrets.json"
    assert path.is_file()
    mode = oct(path.stat().st_mode & 0o777)
    assert mode == oct(0o600)


def test_migrates_legacy_local_api_key_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_secrets_cache()
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    monkeypatch.delenv("SELLERCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SELLERCLAW_HOOKS_TOKEN", raising=False)
    legacy = tmp_path / "local_api_key"
    legacy.write_text("legacy-local\n", encoding="utf-8")
    sec = load_or_create_secrets(tmp_path)
    assert sec.local_api_key == "legacy-local"
    assert not legacy.exists()
    data = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert data["local_api_key"] == "legacy-local"
    assert "gateway_token" in data and len(data["gateway_token"]) > 20
    assert "hooks_token" in data and len(data["hooks_token"]) > 20


def test_clean_generation_without_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_secrets_cache()
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    monkeypatch.delenv("SELLERCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SELLERCLAW_HOOKS_TOKEN", raising=False)
    sec = load_or_create_secrets(tmp_path)
    for k in ("local_api_key", "gateway_token", "hooks_token"):
        assert len(getattr(sec, k)) > 20
    assert (tmp_path / "secrets.json").is_file()


def test_legacy_local_api_key_file_kept_when_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_secrets_cache()
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "env-local")
    monkeypatch.delenv("SELLERCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SELLERCLAW_HOOKS_TOKEN", raising=False)
    legacy = tmp_path / "local_api_key"
    legacy.write_text("legacy-local\n", encoding="utf-8")
    load_or_create_secrets(tmp_path)
    assert legacy.is_file()


def test_regenerates_missing_hooks_when_empty_string_in_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_secrets_cache()
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    monkeypatch.delenv("SELLERCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SELLERCLAW_HOOKS_TOKEN", raising=False)
    (tmp_path / "secrets.json").write_text(
        json.dumps(
            {"local_api_key": "lk", "gateway_token": "gw", "hooks_token": ""},
        ),
        encoding="utf-8",
    )
    sec = load_or_create_secrets(tmp_path)
    assert sec.hooks_token != ""
    assert len(sec.hooks_token) > 20


def test_migrate_legacy_manifest_tokens_into_secrets_writes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_secrets_cache()
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    monkeypatch.delenv("SELLERCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SELLERCLAW_HOOKS_TOKEN", raising=False)
    manifest = {
        "user_id": "u1",
        "gateway_token": "mgw",
        "hooks_token": "mhk",
    }
    cleaned = migrate_legacy_manifest_tokens_into_secrets(tmp_path, manifest)
    assert cleaned == {"user_id": "u1"}
    raw = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert raw["gateway_token"] == "mgw"
    assert raw["hooks_token"] == "mhk"
    assert len(raw["local_api_key"]) > 20
