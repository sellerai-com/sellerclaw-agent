from __future__ import annotations

import pytest

from sellerclaw_agent.server.local_api_key import (
    load_or_create_local_api_key,
    reset_local_api_key_cache,
)

pytestmark = pytest.mark.unit


def test_load_or_create_prefers_env(tmp_path, monkeypatch) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "env-secret")
    assert load_or_create_local_api_key(tmp_path) == "env-secret"


def test_load_or_create_persists_file(tmp_path, monkeypatch) -> None:
    reset_local_api_key_cache()
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    monkeypatch.delenv("SELLERCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SELLERCLAW_HOOKS_TOKEN", raising=False)
    a = load_or_create_local_api_key(tmp_path)
    b = load_or_create_local_api_key(tmp_path)
    assert a == b
    assert len(a) > 20
    assert (tmp_path / "secrets.json").is_file()


def test_file_permissions_best_effort(tmp_path, monkeypatch) -> None:
    reset_local_api_key_cache()
    monkeypatch.delenv("SELLERCLAW_LOCAL_API_KEY", raising=False)
    monkeypatch.delenv("SELLERCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SELLERCLAW_HOOKS_TOKEN", raising=False)
    load_or_create_local_api_key(tmp_path)
    mode = oct((tmp_path / "secrets.json").stat().st_mode & 0o777)
    assert mode == oct(0o600)
