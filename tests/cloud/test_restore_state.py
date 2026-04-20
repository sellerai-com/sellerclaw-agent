from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import Client as HttpxClient
from sellerclaw_agent.cloud.restore_state import run_restore_if_needed
from sellerclaw_agent.cloud.state_backup import build_state_backup_archive

pytestmark = pytest.mark.unit


def test_run_restore_skips_when_local_sessions_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    p = tmp_path / "agents" / "a" / "sessions" / "s.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("x", encoding="utf-8")
    client_ctor = MagicMock()
    monkeypatch.setattr("sellerclaw_agent.cloud.restore_state.httpx.Client", client_ctor)
    run_restore_if_needed()
    client_ctor.assert_not_called()


def test_run_restore_skips_without_agent_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    client_ctor = MagicMock()
    monkeypatch.setattr("sellerclaw_agent.cloud.restore_state.httpx.Client", client_ctor)
    run_restore_if_needed()
    client_ctor.assert_not_called()


def test_run_restore_skips_when_reset_state_after_clean_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clean=True clears state in entrypoint; must not re-fetch S3 backup on same boot."""
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RESET_STATE", "1")
    monkeypatch.setenv("AGENT_API_KEY", "secret-token")
    client_ctor = MagicMock()
    monkeypatch.setattr("sellerclaw_agent.cloud.restore_state.httpx.Client", client_ctor)
    run_restore_if_needed()
    client_ctor.assert_not_called()


def test_run_restore_downloads_when_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_API_KEY", "secret-token")
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://example.com")

    src = tmp_path / "upstream"
    (src / "agents" / "z" / "sessions").mkdir(parents=True)
    (src / "agents" / "z" / "sessions" / "c.jsonl").write_text("{}\n", encoding="utf-8")
    archive = build_state_backup_archive(src, include_chrome=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://example.com/agent/connection/state-backup"
        assert request.headers.get("authorization") == "Bearer secret-token"
        return httpx.Response(200, content=archive)

    def _client_factory(**kwargs: Any) -> httpx.Client:
        kwargs.pop("transport", None)
        return HttpxClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("sellerclaw_agent.cloud.restore_state.httpx.Client", _client_factory)
    run_restore_if_needed()
    assert (tmp_path / "agents" / "z" / "sessions" / "c.jsonl").read_text() == "{}\n"


def test_run_restore_noop_on_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_API_KEY", "t")
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://example.com")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    def _client_factory(**kwargs: Any) -> httpx.Client:
        kwargs.pop("transport", None)
        return HttpxClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("sellerclaw_agent.cloud.restore_state.httpx.Client", _client_factory)
    run_restore_if_needed()
    assert not (tmp_path / "agents").exists()
