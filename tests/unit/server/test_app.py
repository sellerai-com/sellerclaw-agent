from __future__ import annotations

import gzip
import io
import json
import tarfile
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import sellerclaw_agent.server.app as srv_app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.server.app import app, auth_local_bootstrap, get_command_history_storage, get_openclaw_manager, get_storage
from sellerclaw_agent.server.command_history import CommandHistoryStorage
from sellerclaw_agent.server.local_api_key import reset_local_api_key_cache
from sellerclaw_agent.server.secrets_store import reset_secrets_cache
from sellerclaw_agent.server.storage import ManifestStorage

pytestmark = pytest.mark.unit

_LOCAL_API_KEY = "unit-test-local-api-key"
_CONTROL_PLANE_AUTH = {"Authorization": f"Bearer {_LOCAL_API_KEY}"}


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
    reset_local_api_key_cache()
    reset_secrets_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_EDGE_PING", "0")
    monkeypatch.setenv("OPENCLAW_BUNDLE_VOLUME_PATH", str(tmp_path / "bundle"))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", _LOCAL_API_KEY)

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
    response = client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data())
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
    response = client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=payload)
    assert response.status_code == 200
    runtime_env = (tmp_path / "bundle" / "runtime.env").read_text(encoding="utf-8")
    assert "export PROXY_URL='http://u:p@proxy.example:3128'" in runtime_env


def test_post_manifest_legacy_tokens_warn_once_per_process(
    client: TestClient,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    import logging

    from sellerclaw_agent.server import manifest_deprecation as md

    md.reset_manifest_deprecation_warnings()
    caplog.set_level(logging.WARNING)
    payload = make_manifest_data(gateway_token="gw-legacy", hooks_token="hk-legacy")
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=payload).status_code == 200
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert sum("gateway_token" in m for m in warns) == 1
    assert sum("hooks_token" in m for m in warns) == 1
    caplog.clear()
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=payload).status_code == 200
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_post_manifest_accepts_without_legacy_gateway_hooks(
    client: TestClient,
    tmp_path,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    payload = make_manifest_data()
    response = client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=payload)
    assert response.status_code == 200
    written = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert "gateway_token" not in written
    assert "hooks_token" not in written


def test_post_manifest_strips_legacy_web_search_fields(
    client: TestClient,
    tmp_path,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    payload = make_manifest_data(
        web_search={
            "enabled": True,
            "provider": "brave",
            "api_key": "must-not-persist",
            "base_url": "https://ignored.example",
        }
    )
    response = client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=payload)
    assert response.status_code == 200
    written = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert written["web_search"] == {"enabled": True}


def test_get_bundle_archive_includes_sellerclaw_web_search_plugin_config(
    client: TestClient,
    tmp_path,
    monkeypatch,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    monkeypatch.setenv("SELLERCLAW_API_URL", "https://api.example")
    creds = CredentialsStorage(tmp_path)
    creds.save(
        user_id=UUID("11111111-1111-4111-8111-111111111111"),
        user_email="t@example.com",
        user_name="T",
        agent_token="sca_unit_archive_token",
        connected_at="2026-01-01T00:00:00Z",
    )
    payload = make_manifest_data(web_search={"enabled": True})
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=payload).status_code == 200
    response = client.get("/bundle/archive", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 200
    buf = io.BytesIO(response.content)
    with gzip.GzipFile(fileobj=buf, mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as archive:
            oc_raw = archive.extractfile("openclaw/openclaw.json")
            assert oc_raw is not None
            oc = json.loads(oc_raw.read().decode("utf-8"))
    entry = oc["plugins"]["entries"]["sellerclaw-web-search"]
    # Derived SELLERCLAW_AGENT_API_BASE_URL = SELLERCLAW_API_URL + agent_api_base_path
    # (``/agent`` is the default path supplied by the manifest fixture).
    assert entry["config"]["webSearch"]["baseUrl"] == "https://api.example/agent"
    assert entry["config"]["webSearch"]["authToken"] == "sca_unit_archive_token"


def test_post_manifest_bundle_validation_400(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    bad = make_manifest_data(connected_integrations=["not_a_valid_kind"])
    response = client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=bad)
    assert response.status_code == 400
    assert "not_a_valid_kind" in response.json()["detail"]


def test_get_manifest_empty_returns_404(client: TestClient) -> None:
    response = client.get("/manifest", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "manifest_not_found"


def test_get_manifest_after_save_returns_content(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    payload = make_manifest_data()
    post_resp = client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=payload)
    assert post_resp.status_code == 200
    post_version = post_resp.json()["version"]

    get_resp = client.get("/manifest", headers=_CONTROL_PLANE_AUTH)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["version"] == post_version
    assert body["manifest"]["user_id"] == "11111111-1111-4111-8111-111111111111"
    assert "gateway_token" not in body["manifest"]
    assert "hooks_token" not in body["manifest"]
    assert body["manifest"]["models"]["complex"]["id"] == "c1"


def test_get_manifest_strips_tokens_if_reintroduced_on_disk(
    client: TestClient,
    tmp_path,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data()).status_code == 200
    path = tmp_path / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["gateway_token"] = "must-not-leak"
    data["hooks_token"] = "must-not-leak-2"
    path.write_text(json.dumps(data), encoding="utf-8")
    get_resp = client.get("/manifest", headers=_CONTROL_PLANE_AUTH)
    assert get_resp.status_code == 200
    body = get_resp.json()["manifest"]
    assert "gateway_token" not in body
    assert "hooks_token" not in body


def test_post_manifest_validation_422_when_required_field_missing(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    payload = make_manifest_data()
    del payload["litellm_base_url"]
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=payload).status_code == 422


def test_post_manifest_deprecated_tokens_warn_once_each_for_separate_fields(
    client: TestClient,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    import logging

    from sellerclaw_agent.server import manifest_deprecation as md

    md.reset_manifest_deprecation_warnings()
    caplog.set_level(logging.WARNING)
    only_gw = make_manifest_data(gateway_token="g-only")
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=only_gw).status_code == 200
    warns_a = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert sum("gateway_token" in m for m in warns_a) == 1
    assert sum("hooks_token" in m for m in warns_a) == 0
    caplog.clear()
    only_hk = make_manifest_data(hooks_token="h-only")
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=only_hk).status_code == 200
    warns_b = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert sum("hooks_token" in m for m in warns_b) == 1
    assert sum("gateway_token" in m for m in warns_b) == 0


def test_lifespan_migrates_manifest_tokens_off_disk(
    tmp_path,
    monkeypatch,
    openclaw_manager_mock: MagicMock,
    make_manifest_data: Callable[..., dict[str, Any]],
) -> None:
    reset_local_api_key_cache()
    reset_secrets_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_EDGE_PING", "0")
    monkeypatch.setenv("OPENCLAW_BUNDLE_VOLUME_PATH", str(tmp_path / "bundle"))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", _LOCAL_API_KEY)

    seeded = make_manifest_data()
    seeded["gateway_token"] = "from-manifest-gw"
    seeded["hooks_token"] = "from-manifest-hk"
    (tmp_path / "manifest.json").write_text(json.dumps(seeded), encoding="utf-8")

    app.dependency_overrides[get_storage] = lambda: ManifestStorage(tmp_path)
    app.dependency_overrides[get_command_history_storage] = lambda: CommandHistoryStorage(tmp_path)
    app.dependency_overrides[get_openclaw_manager] = lambda: openclaw_manager_mock
    try:
        with TestClient(app) as client:
            on_disk = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
            assert "gateway_token" not in on_disk
            assert "hooks_token" not in on_disk
            secrets = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
            assert secrets["gateway_token"] == "from-manifest-gw"
            assert secrets["hooks_token"] == "from-manifest-hk"
            got = client.get("/manifest", headers=_CONTROL_PLANE_AUTH)
            assert got.status_code == 200
            assert "gateway_token" not in got.json()["manifest"]
            assert "hooks_token" not in got.json()["manifest"]
    finally:
        app.dependency_overrides.clear()


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
        agent_token="sca_unit_test_token",
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
    response = client.get("/commands/history", headers=_CONTROL_PLANE_AUTH)
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

    response = client.get("/commands/history", headers=_CONTROL_PLANE_AUTH)
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
    client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data())
    first = client.get("/manifest", headers=_CONTROL_PLANE_AUTH).json()["version"]
    second = client.get("/manifest", headers=_CONTROL_PLANE_AUTH).json()["version"]
    assert first == second


def test_openclaw_status(
    client: TestClient,
    openclaw_manager_mock: MagicMock,
) -> None:
    response = client.get("/openclaw/status", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stopped"
    assert body["container_name"] == "sellerclaw-openclaw"
    assert body["ports"] == {"gateway": 7788, "vnc": 6080}
    openclaw_manager_mock.get_status_detail.assert_called_once()


def test_openclaw_status_requires_local_api_key_header(
    tmp_path,
    monkeypatch,
    openclaw_manager_mock: MagicMock,
) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_EDGE_PING", "0")
    monkeypatch.setenv("OPENCLAW_BUNDLE_VOLUME_PATH", str(tmp_path / "bundle"))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "secret-local")

    def _storage() -> ManifestStorage:
        return ManifestStorage(tmp_path)

    def _history() -> CommandHistoryStorage:
        return CommandHistoryStorage(tmp_path)

    app.dependency_overrides[get_storage] = _storage
    app.dependency_overrides[get_command_history_storage] = _history
    app.dependency_overrides[get_openclaw_manager] = lambda: openclaw_manager_mock
    try:
        with TestClient(app) as bare_client:
            response = bare_client.get("/openclaw/status")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401


def test_auth_status_requires_local_api_key_header(
    tmp_path,
    monkeypatch,
    openclaw_manager_mock: MagicMock,
) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_EDGE_PING", "0")
    monkeypatch.setenv("OPENCLAW_BUNDLE_VOLUME_PATH", str(tmp_path / "bundle"))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "secret-local")

    def _storage() -> ManifestStorage:
        return ManifestStorage(tmp_path)

    app.dependency_overrides[get_storage] = _storage
    app.dependency_overrides[get_openclaw_manager] = lambda: openclaw_manager_mock
    try:
        with TestClient(app) as bare_client:
            response = bare_client.get("/auth/status")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401


def test_openclaw_status_with_wrong_bearer_returns_401(
    client: TestClient,
) -> None:
    response = client.get("/openclaw/status", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_openclaw_start_requires_manifest(client: TestClient) -> None:
    response = client.post("/openclaw/start", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "manifest_not_found"


def test_openclaw_start_ok(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data()).status_code == 200
    response = client.post("/openclaw/start", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 200
    assert response.json() == {"outcome": "completed", "error": None}
    openclaw_manager_mock.start.assert_called_once()


def test_openclaw_start_rejected(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    from sellerclaw_agent.cloud.supervisor_manager import REJECT_ALREADY_RUNNING

    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data()).status_code == 200
    openclaw_manager_mock.start.return_value = ("rejected", REJECT_ALREADY_RUNNING)
    response = client.post("/openclaw/start", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == REJECT_ALREADY_RUNNING


def test_openclaw_stop_idempotent(
    client: TestClient,
    openclaw_manager_mock: MagicMock,
) -> None:
    response = client.post("/openclaw/stop", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 200
    assert response.json()["outcome"] == "completed"
    openclaw_manager_mock.stop.assert_called_once()


def test_openclaw_restart_ok(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data()).status_code == 200
    response = client.post("/openclaw/restart", headers=_CONTROL_PLANE_AUTH)
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
    response = client.get("/openclaw/status", headers=_CONTROL_PLANE_AUTH)
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
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data()).status_code == 200
    openclaw_manager_mock.start.return_value = ("failed", "supervisor error")
    response = client.post("/openclaw/start", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 200
    assert response.json() == {"outcome": "failed", "error": "supervisor error"}


def test_openclaw_start_reject_message_unknown_code(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data()).status_code == 200
    openclaw_manager_mock.start.return_value = ("rejected", "custom_unknown_code")
    response = client.post("/openclaw/start", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "custom_unknown_code"
    assert detail["message"] == "custom_unknown_code"


def test_openclaw_restart_requires_manifest(client: TestClient) -> None:
    response = client.post("/openclaw/restart", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "manifest_not_found"


def test_openclaw_restart_rejected_409(
    client: TestClient,
    make_manifest_data: Callable[..., dict[str, Any]],
    openclaw_manager_mock: MagicMock,
) -> None:
    from sellerclaw_agent.cloud.supervisor_manager import REJECT_ALREADY_RUNNING

    assert client.post("/manifest", headers=_CONTROL_PLANE_AUTH, json=make_manifest_data()).status_code == 200
    openclaw_manager_mock.restart.return_value = ("rejected", REJECT_ALREADY_RUNNING)
    response = client.post("/openclaw/restart", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == REJECT_ALREADY_RUNNING
    assert "restart" in detail["message"]


def test_openclaw_stop_failed(
    client: TestClient,
    openclaw_manager_mock: MagicMock,
) -> None:
    openclaw_manager_mock.stop.return_value = ("failed", "timeout")
    response = client.post("/openclaw/stop", headers=_CONTROL_PLANE_AUTH)
    assert response.status_code == 200
    assert response.json() == {"outcome": "failed", "error": "timeout"}


def test_auth_local_bootstrap_loopback_ipv4(monkeypatch, tmp_path) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "loop-key")
    req = MagicMock()
    req.client.host = "127.0.0.1"
    assert auth_local_bootstrap(req) == {"local_api_key": "loop-key"}


def test_auth_local_bootstrap_loopback_ipv6(monkeypatch, tmp_path) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "loop-key6")
    req = MagicMock()
    req.client.host = "::1"
    assert auth_local_bootstrap(req) == {"local_api_key": "loop-key6"}


def test_auth_local_bootstrap_loopback_ipv4_mapped(monkeypatch, tmp_path) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "loop-mapped")
    req = MagicMock()
    req.client.host = "::ffff:127.0.0.1"
    assert auth_local_bootstrap(req) == {"local_api_key": "loop-mapped"}


def test_auth_local_bootstrap_loopback_class_b(monkeypatch, tmp_path) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "loop-b")
    req = MagicMock()
    req.client.host = "127.2.3.4"
    assert auth_local_bootstrap(req) == {"local_api_key": "loop-b"}


def test_auth_local_bootstrap_non_loopback_forbidden(monkeypatch, tmp_path) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    req = MagicMock()
    req.client.host = "203.0.113.1"
    with pytest.raises(HTTPException) as exc_info:
        auth_local_bootstrap(req)
    assert exc_info.value.status_code == 403


def test_auth_local_bootstrap_docker_published_host_gateway_ok(monkeypatch, tmp_path) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "gw-key")
    monkeypatch.setattr("sellerclaw_agent.server.app._running_inside_docker", lambda: True)
    monkeypatch.setattr(
        "sellerclaw_agent.server.app._default_ipv4_gateway_linux",
        lambda: "172.17.0.1",
    )
    req = MagicMock()
    req.client.host = "172.17.0.1"
    assert auth_local_bootstrap(req) == {"local_api_key": "gw-key"}


def test_auth_local_bootstrap_docker_published_host_gateway_ipv4_mapped(
    monkeypatch, tmp_path
) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "gw-mapped")
    monkeypatch.setattr("sellerclaw_agent.server.app._running_inside_docker", lambda: True)
    monkeypatch.setattr(
        "sellerclaw_agent.server.app._default_ipv4_gateway_linux",
        lambda: "172.17.0.1",
    )
    req = MagicMock()
    req.client.host = "::ffff:172.17.0.1"
    assert auth_local_bootstrap(req) == {"local_api_key": "gw-mapped"}


def test_auth_local_bootstrap_docker_sibling_container_forbidden(monkeypatch, tmp_path) -> None:
    reset_local_api_key_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("sellerclaw_agent.server.app._running_inside_docker", lambda: True)
    monkeypatch.setattr(
        "sellerclaw_agent.server.app._default_ipv4_gateway_linux",
        lambda: "172.17.0.1",
    )
    req = MagicMock()
    req.client.host = "172.18.0.5"
    with pytest.raises(HTTPException) as exc_info:
        auth_local_bootstrap(req)
    assert exc_info.value.status_code == 403
