"""The local routes behind ``sellerclaw-agent connect chatgpt`` and ``disconnect``."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sellerclaw_agent.server.ai_subscription_routes as routes
from sellerclaw_agent.server.secrets_store import reset_secrets_cache
from sellerclaw_agent.server.storage import ManifestStorage
from sellerclaw_agent.subscription import chatgpt_auth, claude_cli
from sellerclaw_agent.subscription.state import load_choice, save_choice
from sellerclaw_agent.test_manifest_fixtures import load_manifest_v2_mapping

pytestmark = pytest.mark.unit

_AUTH = {"Authorization": "Bearer local"}
_PROFILE = {"type": "oauth", "provider": "openai", "access": "acc", "refresh": "ref", "accountId": "a"}


class _FakeManager:
    """Records how the agent applied a change: a config reload, or a gateway restart.

    ``fails`` — the error a reload / restart answers with instead of completing;
    ``openclaw_status`` — what the supervisor probe reports (running by default).
    """

    def __init__(self, *, fails: str | None = None, openclaw_status: str = "running") -> None:
        self.calls: list[str] = []
        self.fails = fails
        self.openclaw_status = openclaw_status

    def probe_openclaw_status(self) -> tuple[str, str | None]:
        return self.openclaw_status, None

    def _outcome(self) -> tuple[str, str | None]:
        return ("failed", self.fails) if self.fails else ("completed", None)

    def update_manifest(self, manifest: Any) -> tuple[str, str | None]:
        self.calls.append("reload")
        return self._outcome()

    def restart(self, manifest: Any) -> tuple[str, str | None]:
        self.calls.append("restart")
        return self._outcome()


def _openclaw_state(root: Path, *, signed_in: bool) -> Path:
    """OpenClaw's main-agent store, holding a fresh device-code login or nothing."""
    db = root / "agents" / "supervisor" / "agent" / "openclaw-agent.sqlite"
    db.parent.mkdir(parents=True)
    profiles = {"openai:default": _PROFILE} if signed_in else {}
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE auth_profile_store (store_key TEXT NOT NULL PRIMARY KEY, store_json TEXT NOT NULL, "
            "updated_at INTEGER NOT NULL) STRICT"
        )
        conn.execute(
            "INSERT INTO auth_profile_store VALUES ('primary', ?, 1)",
            (json.dumps({"version": 1, "profiles": profiles}),),
        )
    return root


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    reset_secrets_cache()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(data))
    (data / "secrets.json").write_text(
        json.dumps({"local_api_key": "local", "gateway_token": "gw", "hooks_token": "hooks"}), encoding="utf-8"
    )
    ManifestStorage(data).save(load_manifest_v2_mapping())
    return data


@pytest.fixture()
def manager() -> _FakeManager:
    return _FakeManager()


@pytest.fixture()
def signed_out_of_claude(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    monkeypatch.setattr(claude_cli, "logout", calls.append)
    return calls


def _client(data_dir: Path, manager: _FakeManager, state_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(routes.owner_router)
    app.dependency_overrides[routes._data_dir] = lambda: data_dir
    app.dependency_overrides[routes._manager] = lambda: manager
    app.dependency_overrides[routes._state_dir] = lambda: state_dir
    return TestClient(app)


def test_prepare_holds_the_chatgpt_provider_on_for_the_sign_in(
    data_dir: Path, manager: _FakeManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "_wait_for_openai_provider", lambda timeout_s: True)
    client = _client(data_dir, manager, _openclaw_state(tmp_path / "oc", signed_in=False))

    response = client.post("/ai-subscription/chatgpt/prepare", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"ready": True, "error": None}
    assert chatgpt_auth.login_pending(data_dir)
    assert manager.calls == ["reload"]


def test_cancel_lets_the_provider_go(data_dir: Path, manager: _FakeManager, tmp_path: Path) -> None:
    chatgpt_auth.mark_login_pending(data_dir)
    client = _client(data_dir, manager, _openclaw_state(tmp_path / "oc", signed_in=False))

    response = client.post("/ai-subscription/chatgpt/cancel", headers=_AUTH)

    assert response.status_code == 200
    assert not chatgpt_auth.login_pending(data_dir)
    assert manager.calls == ["reload"]
    assert load_choice(data_dir) is None


def test_activate_refuses_until_chatgpt_is_signed_in(data_dir: Path, manager: _FakeManager, tmp_path: Path) -> None:
    client = _client(data_dir, manager, _openclaw_state(tmp_path / "oc", signed_in=False))

    response = client.post("/ai-subscription/chatgpt/activate", headers=_AUTH)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "chatgpt_not_signed_in",
        "message": "ChatGPT is not signed in on this computer yet.",
    }
    assert load_choice(data_dir) is None
    assert manager.calls == []


def test_activate_switches_from_claude_keeps_a_copy_and_restarts(
    data_dir: Path, manager: _FakeManager, tmp_path: Path, signed_out_of_claude: list[Path]
) -> None:
    """One plan at a time: Claude is signed out only once ChatGPT works; the restart shares the sign-in."""
    save_choice(data_dir, "claude")
    chatgpt_auth.mark_login_pending(data_dir)
    client = _client(data_dir, manager, _openclaw_state(tmp_path / "oc", signed_in=True))

    response = client.post("/ai-subscription/chatgpt/activate", headers=_AUTH)

    assert response.status_code == 200
    assert response.json()["provider"] == "chatgpt"
    choice = load_choice(data_dir)
    assert choice is not None and choice.provider == "chatgpt"
    assert not chatgpt_auth.login_pending(data_dir)
    assert chatgpt_auth.load_snapshot(data_dir).profiles == {"openai:default": _PROFILE}
    assert signed_out_of_claude == [data_dir]
    assert manager.calls == ["restart"]


@pytest.mark.parametrize(
    ("provider", "openclaw_status", "applied_by"),
    [
        # ChatGPT's sign-in leaves OpenClaw's store only while the gateway is stopped.
        pytest.param("chatgpt", "running", "restart", id="chatgpt-restarts"),
        pytest.param("chatgpt", "starting", "restart", id="chatgpt-coming-up-restarts"),
        # "Switch to credits" from the web panel must not start an agent its owner stopped; its next
        # start settles the sign-in anyway.
        pytest.param("chatgpt", "stopped", "reload", id="chatgpt-stopped-stays-stopped"),
        pytest.param("chatgpt", "error", "reload", id="chatgpt-failed-to-start-stays-down"),
        pytest.param("claude", "running", "reload", id="claude-reloads"),
    ],
)
def test_disconnect_signs_the_plan_out(
    data_dir: Path,
    manager: _FakeManager,
    tmp_path: Path,
    signed_out_of_claude: list[Path],
    provider: str,
    openclaw_status: str,
    applied_by: str,
) -> None:
    manager.openclaw_status = openclaw_status
    save_choice(data_dir, provider)
    state_dir = _openclaw_state(tmp_path / "oc", signed_in=True)
    chatgpt_auth.snapshot(data_dir, state_dir)
    client = _client(data_dir, manager, state_dir)

    response = client.post("/ai-subscription/disconnect", headers=_AUTH)

    assert response.status_code == 200
    assert load_choice(data_dir) is None
    assert manager.calls == [applied_by]
    if provider == "chatgpt":
        assert not chatgpt_auth.snapshot_path(data_dir).exists()
        assert signed_out_of_claude == []
    else:
        assert signed_out_of_claude == [data_dir]


def test_a_failed_restart_keeps_the_previous_plan_signed_in(
    data_dir: Path, tmp_path: Path, signed_out_of_claude: list[Path]
) -> None:
    """The agent stays on its old config when the restart fails — and that config needs Claude."""
    save_choice(data_dir, "claude")
    manager = _FakeManager(fails="supervisorctl: ERROR (spawn error)")
    client = _client(data_dir, manager, _openclaw_state(tmp_path / "oc", signed_in=True))

    response = client.post("/ai-subscription/chatgpt/activate", headers=_AUTH)

    assert response.status_code == 200
    assert response.json()["error"] == "supervisorctl: ERROR (spawn error)"
    assert response.json()["applied"] is False
    assert signed_out_of_claude == []


def test_leaving_chatgpt_for_claude_restarts_so_the_sign_in_really_goes(
    data_dir: Path, manager: _FakeManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_cli,
        "auth_status",
        lambda data_dir: claude_cli.ClaudeAuthStatus(logged_in=True, auth_method="claude.ai", subscription_type="max"),
    )
    save_choice(data_dir, "chatgpt")
    state_dir = _openclaw_state(tmp_path / "oc", signed_in=True)
    chatgpt_auth.snapshot(data_dir, state_dir)
    chatgpt_auth.mark_login_pending(data_dir)
    client = _client(data_dir, manager, state_dir)

    response = client.post("/ai-subscription/claude/activate", headers=_AUTH)

    assert response.status_code == 200
    assert manager.calls == ["restart"]
    choice = load_choice(data_dir)
    assert choice is not None and choice.provider == "claude"
    assert not chatgpt_auth.snapshot_path(data_dir).exists()
    assert not chatgpt_auth.login_pending(data_dir)


def test_a_hanging_claude_sign_out_does_not_fail_the_switch(
    data_dir: Path, manager: _FakeManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hangs(data_dir: Path) -> None:
        raise subprocess.TimeoutExpired(cmd="claude auth logout", timeout=20)

    monkeypatch.setattr(claude_cli, "logout", hangs)
    save_choice(data_dir, "claude")
    client = _client(data_dir, manager, _openclaw_state(tmp_path / "oc", signed_in=True))

    response = client.post("/ai-subscription/chatgpt/activate", headers=_AUTH)

    assert response.status_code == 200
    assert response.json()["provider"] == "chatgpt"
    assert response.json()["error"] is None


def test_disconnect_lets_an_abandoned_chatgpt_sign_in_go(
    data_dir: Path, manager: _FakeManager, tmp_path: Path
) -> None:
    save_choice(data_dir, "chatgpt")
    chatgpt_auth.mark_login_pending(data_dir)
    client = _client(data_dir, manager, _openclaw_state(tmp_path / "oc", signed_in=True))

    assert client.post("/ai-subscription/disconnect", headers=_AUTH).status_code == 200
    assert not chatgpt_auth.login_pending(data_dir)


@pytest.mark.parametrize(
    ("configured", "offered"),
    [
        pytest.param(True, False, id="configured-without-a-lineup"),
        pytest.param(False, None, id="no-configuration-yet-is-unknown"),
    ],
)
def test_whether_the_plan_is_offered_is_unknown_until_the_cloud_configures_the_agent(
    data_dir: Path, manager: _FakeManager, tmp_path: Path, configured: bool, offered: bool | None
) -> None:
    save_choice(data_dir, "chatgpt")
    if not configured:
        ManifestStorage(data_dir).manifest_path.unlink()
    client = _client(data_dir, manager, _openclaw_state(tmp_path / "oc", signed_in=True))

    assert client.get("/ai-subscription", headers=_AUTH).json()["offered"] is offered
