from __future__ import annotations

import json
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import sellerclaw_agent.server.app as srv_app
from fastapi.testclient import TestClient
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.server.app import app, get_command_history_storage, get_openclaw_manager, get_storage
from sellerclaw_agent.server.command_history import CommandHistoryStorage
from sellerclaw_agent.server.storage import ManifestStorage

pytestmark = pytest.mark.unit

_AGENT_API_KEY = "unit-test-agent-api-key"
_AGENT_AUTH = {"Authorization": f"Bearer {_AGENT_API_KEY}"}


@pytest.fixture()
def openclaw_manager_mock() -> MagicMock:
    m = MagicMock()
    m.get_status_detail.return_value = {
        "status": "stopped",
        "container_name": "sellerclaw-openclaw",
        "container_id": None,
        "image": None,
        "uptime_seconds": None,
        "ports": {"gateway": 7788, "vnc": 6080},
        "error": None,
    }
    m.start.return_value = ("completed", None)
    m.stop.return_value = ("completed", None)
    m.restart.return_value = ("completed", None)
    return m


@pytest.fixture()
def client(
    tmp_path,
    monkeypatch,
    openclaw_manager_mock: MagicMock,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_EDGE_PING", "0")
    monkeypatch.setenv("OPENCLAW_BUNDLE_VOLUME_PATH", str(tmp_path / "bundle"))
    monkeypatch.setenv("AGENT_API_KEY", _AGENT_API_KEY)

    def _storage() -> ManifestStorage:
        return ManifestStorage(tmp_path)

    def _history() -> CommandHistoryStorage:
        return CommandHistoryStorage(tmp_path)

    app.dependency_overrides[get_storage] = _storage
    app.dependency_overrides[get_command_history_storage] = _history
    app.dependency_overrides[get_openclaw_manager] = lambda: openclaw_manager_mock
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_manifest_persists_file(
    client: TestClient,
    tmp_path,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    response = client.post("/manifest", headers=_AGENT_AUTH, json=make_manifest_data())
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["manifest_path"] == str(tmp_path / "manifest.json")
    assert len(payload["version"]) == 16
    written = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert written["user_id"] == "11111111-1111-4111-8111-111111111111"
    assert UUID(str(written["user_id"])) == UUID("11111111-1111-4111-8111-111111111111")
    assert not (tmp_path / "manifest.json.tmp").exists()


def test_post_manifest_writes_runtime_env_from_proxy_url(
    client: TestClient,
    tmp_path,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    """POST /manifest materializes PROXY_URL into runtime.env so gost/chrome see it."""
    payload = make_manifest_data(proxy_url="http://u:p@proxy.example:3128")
    response = client.post("/manifest", headers=_AGENT_AUTH, json=payload)
    assert response.status_code == 200
    runtime_env = (tmp_path / "bundle" / "runtime.env").read_text(encoding="utf-8")
    assert "export PROXY_URL='http://u:p@proxy.example:3128'" in runtime_env


def test_post_manifest_validation_error_422(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    bad = make_manifest_data()
    del bad["gateway_token"]
    response = client.post("/manifest", headers=_AGENT_AUTH, json=bad)
    assert response.status_code == 422


def test_post_manifest_bundle_validation_400(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    bad = make_manifest_data(connected_integrations=["not_a_valid_kind"])
    response = client.post("/manifest", headers=_AGENT_AUTH, json=bad)
    assert response.status_code == 400
    assert "not_a_valid_kind" in response.json()["detail"]


def test_get_manifest_empty_returns_404(client: TestClient) -> None:
    response = client.get("/manifest", headers=_AGENT_AUTH)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "manifest_not_found"


def test_get_manifest_after_save_returns_content(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    payload = make_manifest_data()
    post_resp = client.post("/manifest", headers=_AGENT_AUTH, json=payload)
    assert post_resp.status_code == 200
    post_version = post_resp.json()["version"]

    get_resp = client.get("/manifest", headers=_AGENT_AUTH)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["version"] == post_version
    assert body["manifest"]["user_id"] == "11111111-1111-4111-8111-111111111111"
    assert body["manifest"]["gateway_token"] == "g"
    assert body["manifest"]["models"]["complex"]["id"] == "c1"


def test_get_health_edge_ping_disabled(
    client: TestClient,
    openclaw_manager_mock: MagicMock,
    monkeypatch,
) -> None:
    monkeypatch.setattr(srv_app, "get_openclaw_manager", lambda: openclaw_manager_mock)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["edge_ping_enabled"] is False
    assert body["status"] == "healthy"
    assert "openclaw" in body
    assert body["openclaw"]["status"] == "stopped"
    assert body["session"]["connected"] is False


def test_get_health_unhealthy_when_ping_not_alive(
    tmp_path,
    monkeypatch,
    openclaw_manager_mock: MagicMock,
) -> None:
    from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_EDGE_PING", "0")

    def _storage() -> ManifestStorage:
        return ManifestStorage(tmp_path)

    app.dependency_overrides[get_storage] = _storage
    app.dependency_overrides[get_openclaw_manager] = lambda: openclaw_manager_mock
    reg = EdgeRuntimeRegistry()
    reg.mark_task_alive("ping_loop", alive=False)
    monkeypatch.setattr(
        "sellerclaw_agent.server.runtime_registry.get_runtime_registry",
        lambda: reg,
    )
    try:
        with TestClient(app) as client:
            body = client.get("/health").json()
            assert body["status"] == "unhealthy"
            assert body["edge_ping_enabled"] is True
            assert body["tasks"]["ping_loop"]["alive"] is False
    finally:
        app.dependency_overrides.clear()


def test_get_health_degraded_when_command_executor_dead(
    tmp_path,
    monkeypatch,
    openclaw_manager_mock: MagicMock,
) -> None:
    from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_EDGE_PING", "0")

    uid = UUID("11111111-1111-4111-8111-111111111111")
    CredentialsStorage(tmp_path).save(
        user_id=uid,
        user_email="a@example.com",
        user_name="u",
        access_token="tok",
        refresh_token="ref",
        connected_at=datetime.now(tz=UTC).isoformat(),
    )
    EdgeSessionStorage(tmp_path).save(
        agent_instance_id=UUID("22222222-2222-4222-8222-222222222222"),
        protocol_version=1,
    )

    app.dependency_overrides[get_storage] = lambda: ManifestStorage(tmp_path)
    app.dependency_overrides[get_openclaw_manager] = lambda: openclaw_manager_mock

    reg = EdgeRuntimeRegistry()
    reg.mark_task_alive("command_executor", alive=False)
    monkeypatch.setattr(
        "sellerclaw_agent.server.runtime_registry.get_runtime_registry",
        lambda: reg,
    )
    try:
        with TestClient(app) as client:
            body = client.get("/health").json()
            assert body["status"] == "degraded"
            assert body["edge_ping_enabled"] is True
            assert body["session"]["connected"] is True
            assert body["tasks"]["ping_loop"]["alive"] is True
            assert body["tasks"]["chat_sse"]["alive"] is True
            assert body["tasks"]["command_executor"]["alive"] is False
    finally:
        app.dependency_overrides.clear()


def test_get_command_history_empty(client: TestClient) -> None:
    response = client.get("/commands/history")
    assert response.status_code == 200
    assert response.json() == {"entries": []}


def test_get_command_history_returns_stored_entries(client: TestClient, tmp_path) -> None:
    storage = CommandHistoryStorage(tmp_path)
    storage.append(
        {
            "command_id": "a",
            "command_type": "start",
            "issued_at": "2026-04-15T10:00:00+00:00",
            "received_at": "2026-04-15T10:00:01+00:00",
            "executed_at": "2026-04-15T10:00:02+00:00",
            "outcome": "ok",
            "error": None,
        }
    )
    storage.append(
        {
            "command_id": "b",
            "command_type": "restart",
            "issued_at": "2026-04-15T11:00:00+00:00",
            "received_at": "2026-04-15T11:00:01+00:00",
            "executed_at": "2026-04-15T11:00:02+00:00",
            "outcome": "error",
            "error": "boom",
        }
    )

    response = client.get("/commands/history")
    assert response.status_code == 200
    body = response.json()
    ids = [e["command_id"] for e in body["entries"]]
    assert ids == ["b", "a"]
    assert body["entries"][0]["outcome"] == "error"
    assert body["entries"][0]["error"] == "boom"
    assert body["entries"][1]["command_type"] == "start"


def test_get_manifest_version_stable_across_calls(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    client.post("/manifest", headers=_AGENT_AUTH, json=make_manifest_data())
    first = client.get("/manifest", headers=_AGENT_AUTH).json()["version"]
    second = client.get("/manifest", headers=_AGENT_AUTH).json()["version"]
    assert first == second


def test_openclaw_status(
    client: TestClient,
    openclaw_manager_mock: MagicMock,
) -> None:
    response = client.get("/openclaw/status", headers=_AGENT_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stopped"
    assert body["container_name"] == "sellerclaw-openclaw"
    assert body["ports"] == {"gateway": 7788, "vnc": 6080}
    openclaw_manager_mock.get_status_detail.assert_called_once()


def test_openclaw_start_requires_manifest(client: TestClient) -> None:
    response = client.post("/openclaw/start", headers=_AGENT_AUTH)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "manifest_not_found"


def test_openclaw_start_ok(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    assert client.post("/manifest", headers=_AGENT_AUTH, json=make_manifest_data()).status_code == 200
    response = client.post("/openclaw/start", headers=_AGENT_AUTH)
    assert response.status_code == 200
    assert response.json() == {"outcome": "completed", "error": None}
    openclaw_manager_mock.start.assert_called_once()


def test_openclaw_start_rejected(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    from sellerclaw_agent.cloud.supervisor_manager import REJECT_ALREADY_RUNNING

    assert client.post("/manifest", headers=_AGENT_AUTH, json=make_manifest_data()).status_code == 200
    openclaw_manager_mock.start.return_value = ("rejected", REJECT_ALREADY_RUNNING)
    response = client.post("/openclaw/start", headers=_AGENT_AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == REJECT_ALREADY_RUNNING


def test_openclaw_stop_idempotent(
    client: TestClient,
    openclaw_manager_mock: MagicMock,
) -> None:
    response = client.post("/openclaw/stop", headers=_AGENT_AUTH)
    assert response.status_code == 200
    assert response.json()["outcome"] == "completed"
    openclaw_manager_mock.stop.assert_called_once()


def test_openclaw_restart_ok(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    assert client.post("/manifest", headers=_AGENT_AUTH, json=make_manifest_data()).status_code == 200
    response = client.post("/openclaw/restart", headers=_AGENT_AUTH)
    assert response.status_code == 200
    assert response.json()["outcome"] == "completed"
    openclaw_manager_mock.restart.assert_called_once()


def test_openclaw_status_running(
    client: TestClient,
    openclaw_manager_mock: MagicMock,
) -> None:
    openclaw_manager_mock.get_status_detail.return_value = {
        "status": "running",
        "container_name": "sellerclaw-openclaw",
        "container_id": "abc123",
        "image": "oc:test",
        "uptime_seconds": 12.5,
        "ports": {"gateway": 7788, "vnc": 6080},
        "error": None,
    }
    response = client.get("/openclaw/status", headers=_AGENT_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["container_id"] == "abc123"
    assert body["image"] == "oc:test"
    assert body["uptime_seconds"] == 12.5
    assert body["error"] is None


def test_openclaw_start_failed(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    assert client.post("/manifest", headers=_AGENT_AUTH, json=make_manifest_data()).status_code == 200
    openclaw_manager_mock.start.return_value = ("failed", "supervisor error")
    response = client.post("/openclaw/start", headers=_AGENT_AUTH)
    assert response.status_code == 200
    assert response.json() == {"outcome": "failed", "error": "supervisor error"}


def test_openclaw_start_reject_message_unknown_code(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    assert client.post("/manifest", headers=_AGENT_AUTH, json=make_manifest_data()).status_code == 200
    openclaw_manager_mock.start.return_value = ("rejected", "custom_unknown_code")
    response = client.post("/openclaw/start", headers=_AGENT_AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "custom_unknown_code"
    assert detail["message"] == "custom_unknown_code"


def test_openclaw_restart_requires_manifest(client: TestClient) -> None:
    response = client.post("/openclaw/restart", headers=_AGENT_AUTH)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "manifest_not_found"


def test_openclaw_restart_rejected_409(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    from sellerclaw_agent.cloud.supervisor_manager import REJECT_ALREADY_RUNNING

    assert client.post("/manifest", headers=_AGENT_AUTH, json=make_manifest_data()).status_code == 200
    openclaw_manager_mock.restart.return_value = ("rejected", REJECT_ALREADY_RUNNING)
    response = client.post("/openclaw/restart", headers=_AGENT_AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == REJECT_ALREADY_RUNNING
    assert "restart" in detail["message"]


def test_openclaw_stop_failed(
    client: TestClient,
    openclaw_manager_mock: MagicMock,
) -> None:
    openclaw_manager_mock.stop.return_value = ("failed", "timeout")
    response = client.post("/openclaw/stop", headers=_AGENT_AUTH)
    assert response.status_code == 200
    assert response.json() == {"outcome": "failed", "error": "timeout"}
