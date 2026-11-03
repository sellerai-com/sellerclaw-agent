from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pytest import MonkeyPatch
from sellerclaw_agent.cloud.connection_client import SellerClawConnectionClient
from sellerclaw_agent.cloud.credentials import CredentialsStorage

pytestmark = pytest.mark.unit


def _json_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


def _write_creds(tmp_path: Path) -> CredentialsStorage:
    storage = CredentialsStorage(tmp_path)
    storage.save(
        user_id=UUID("11111111-1111-4111-8111-111111111111"),
        user_email="e@example.com",
        user_name="E",
        agent_token="access-1",
        connected_at="2026-01-01T00:00:00Z",
    )
    return storage


@pytest.mark.asyncio
async def test_connect_success(tmp_path: Path) -> None:
    instance = "22222222-2222-4222-8222-222222222222"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/connect"
        assert request.headers.get("authorization") == "Bearer access-1"
        body = _json_body(request)
        assert body["agent_version"] == "9.9.9"
        assert body["protocol_version"] == 1
        return httpx.Response(200, json={"agent_instance_id": instance})

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.connect(agent_version="9.9.9", protocol_version=1)
    assert out.agent_instance_id == UUID(instance)


@pytest.mark.asyncio
async def test_connect_refused_by_another_active_session_raises_specific_error(tmp_path: Path) -> None:
    """409 ``agent_connection_already_active`` is the replaced-instance signal, not a generic error."""
    from sellerclaw_agent.cloud.exceptions import (
        CloudConnectionAlreadyActiveError,
        CloudConnectionError,
    )

    body = {
        "detail": {
            "code": "agent_connection_already_active",
            "message": "Another edge agent session is already connected. Disconnect it first.",
        }
    }
    client = SellerClawConnectionClient(
        credentials_storage=_write_creds(tmp_path),
        base_url="http://example",
        transport=httpx.MockTransport(lambda _r: httpx.Response(409, json=body)),
    )
    with pytest.raises(CloudConnectionAlreadyActiveError, match="Another edge agent session"):
        await client.connect(agent_version="1", protocol_version=7)
    # Still a CloudConnectionError, so every existing handler keeps catching it.
    assert issubclass(CloudConnectionAlreadyActiveError, CloudConnectionError)


@pytest.mark.asyncio
async def test_connect_other_409_stays_a_generic_connection_error(tmp_path: Path) -> None:
    from sellerclaw_agent.cloud.exceptions import (
        CloudConnectionAlreadyActiveError,
        CloudConnectionError,
    )

    body = {"detail": {"code": "agent_command_conflict", "message": "busy"}}
    client = SellerClawConnectionClient(
        credentials_storage=_write_creds(tmp_path),
        base_url="http://example",
        transport=httpx.MockTransport(lambda _r: httpx.Response(409, json=body)),
    )
    with pytest.raises(CloudConnectionError, match="busy") as excinfo:
        await client.connect(agent_version="1", protocol_version=7)
    assert not isinstance(excinfo.value, CloudConnectionAlreadyActiveError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs", "path", "expected_body"),
    [
        pytest.param(
            "post_cron_command_result",
            {"request_id": "req-1", "result": {"ok": True, "error": None, "store_epoch": "e", "job": None, "run_id": None}},
            "/agent/cron/commands/req-1",
            {"ok": True, "error": None, "store_epoch": "e", "job": None, "run_id": None},
            id="command-result",
        ),
        pytest.param(
            "post_cron_runs",
            {"runs": [{"jobId": "j1", "runAtMs": 5, "status": "ok"}]},
            "/agent/cron/runs",
            {"runs": [{"jobId": "j1", "runAtMs": 5, "status": "ok"}]},
            id="runs",
        ),
    ],
)
async def test_cron_reports_post_json_with_agent_bearer(
    tmp_path: Path, method: str, kwargs: dict, path: str, expected_body: dict
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = _json_body(request)
        return httpx.Response(204)

    client = SellerClawConnectionClient(
        credentials_storage=_write_creds(tmp_path),
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    assert await getattr(client, method)(**kwargs) is True
    assert seen == {"path": path, "auth": "Bearer access-1", "body": expected_body}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 405, 501])
async def test_cron_reports_to_an_older_cloud_are_dropped_not_failed(tmp_path: Path, status: int) -> None:
    client = SellerClawConnectionClient(
        credentials_storage=_write_creds(tmp_path),
        base_url="http://example",
        transport=httpx.MockTransport(lambda _r: httpx.Response(status)),
    )
    assert await client.post_cron_runs([{"jobId": "j1", "runAtMs": 1, "status": "ok"}]) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "match"),
    [
        pytest.param(503, None, "HTTP 503", id="server-error"),
        pytest.param(422, {"detail": "bad runs"}, "bad runs", id="validation"),
    ],
)
async def test_cron_reports_raise_on_failures(
    tmp_path: Path, status: int, body: dict | None, match: str
) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudConnectionError

    client = SellerClawConnectionClient(
        credentials_storage=_write_creds(tmp_path),
        base_url="http://example",
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(status, json=body) if body is not None else httpx.Response(status)
        ),
    )
    with pytest.raises(CloudConnectionError, match=match):
        await client.post_cron_runs([])


@pytest.mark.asyncio
async def test_ping_401_raises_cloud_auth_error(tmp_path: Path) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudAuthError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            return httpx.Response(401, json={"detail": {"message": "expired"}})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudAuthError, match="expired"):
        await client.ping(
            agent_instance_id=UUID("44444444-4444-4444-8444-444444444444"),
            agent_version="1",
            protocol_version=1,
            openclaw_status="stopped",
            openclaw_error=None,
            command_result=None,
        )


@pytest.mark.asyncio
async def test_ping_401_with_session_invalidated_code_raises_session_error(
    tmp_path: Path,
) -> None:
    from sellerclaw_agent.cloud.exceptions import (
        CloudAuthError,
        CloudSessionInvalidatedError,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            return httpx.Response(
                401,
                json={
                    "detail": {
                        "code": "agent_session_invalidated",
                        "message": "stale",
                    }
                },
            )
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudSessionInvalidatedError) as excinfo:
        await client.ping(
            agent_instance_id=UUID("44444444-4444-4444-8444-444444444444"),
            agent_version="1",
            protocol_version=1,
            openclaw_status="stopped",
            openclaw_error=None,
            command_result=None,
        )
    assert isinstance(excinfo.value, CloudAuthError)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_fetch_edge_manifest_success(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/edge-manifest"
        assert request.headers.get("authorization") == "Bearer access-1"
        return httpx.Response(
            200,
            json={"user_id": "11111111-1111-4111-8111-111111111111", "model_name_prefix": "u:pref/"},
        )

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    data = await client.fetch_edge_manifest()
    assert data["model_name_prefix"] == "u:pref/"


@pytest.mark.asyncio
async def test_disconnect_success(tmp_path: Path) -> None:
    instance = "55555555-5555-4555-8555-555555555555"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/disconnect"
        body = _json_body(request)
        assert body["agent_instance_id"] == instance
        assert body["revoke_tokens"] is True
        return httpx.Response(200, json={"status": "ok"})

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    await client.disconnect(agent_instance_id=UUID(instance))


@pytest.mark.asyncio
async def test_disconnect_keeps_tokens_when_revoke_false(tmp_path: Path) -> None:
    instance = "66666666-6666-4666-8666-666666666666"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/disconnect"
        body = _json_body(request)
        assert body["agent_instance_id"] == instance
        assert body["revoke_tokens"] is False
        return httpx.Response(200, json={"status": "ok"})

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    await client.disconnect(agent_instance_id=UUID(instance), revoke_tokens=False)


@pytest.mark.asyncio
async def test_connect_401_raises_cloud_auth_error(tmp_path: Path) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudAuthError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": {"message": "Invalid token"}})

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudAuthError, match="Invalid token"):
        await client.connect(agent_version="1", protocol_version=1)


@pytest.mark.asyncio
async def test_ping_no_pending_command(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            return httpx.Response(200, json={"pending_command": None})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.ping(
        agent_instance_id=UUID("66666666-6666-4666-8666-666666666666"),
        agent_version="1",
        protocol_version=1,
        openclaw_status="running",
        openclaw_error=None,
        command_result=None,
    )
    assert out.pending_command is None


@pytest.mark.asyncio
async def test_ping_includes_openclaw_version_when_provided(tmp_path: Path) -> None:
    seen: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            seen["body"] = _json_body(request)
            return httpx.Response(200, json={"pending_command": None})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    await client.ping(
        agent_instance_id=UUID("66666666-6666-4666-8666-666666666666"),
        agent_version="1",
        protocol_version=1,
        openclaw_status="running",
        openclaw_error=None,
        command_result=None,
        openclaw_version="2026.6.5",
    )
    assert seen["body"]["openclaw_version"] == "2026.6.5"


@pytest.mark.asyncio
async def test_ping_omits_openclaw_version_when_absent(tmp_path: Path) -> None:
    seen: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            seen["body"] = _json_body(request)
            return httpx.Response(200, json={"pending_command": None})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    await client.ping(
        agent_instance_id=UUID("66666666-6666-4666-8666-666666666666"),
        agent_version="1",
        protocol_version=1,
        openclaw_status="running",
        openclaw_error=None,
        command_result=None,
    )
    assert "openclaw_version" not in seen["body"]


@pytest.mark.asyncio
async def test_upload_state_backup_returns_true_on_204(tmp_path: Path) -> None:
    payload = b"\x1f\x8b\x08fake"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/state-backup"
        assert request.method == "POST"
        assert request.headers.get("authorization") == "Bearer access-1"
        assert request.headers.get("content-type") == "application/gzip"
        assert request.content == payload
        return httpx.Response(204)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    assert await client.upload_state_backup(payload) is True


@pytest.mark.asyncio
async def test_download_state_backup_returns_bytes_on_200(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/state-backup"
        assert request.method == "GET"
        assert request.headers.get("authorization") == "Bearer access-1"
        return httpx.Response(200, content=b"archive-bytes")

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    assert await client.download_state_backup() == b"archive-bytes"


@pytest.mark.asyncio
async def test_download_state_backup_returns_none_on_404(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    assert await client.download_state_backup() is None


@pytest.mark.asyncio
async def test_connect_uses_agent_api_key_when_no_credentials_json(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    instance = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/connect"
        assert request.headers.get("authorization") == "Bearer sca_test_token_123"
        return httpx.Response(200, json={"agent_instance_id": instance})

    storage = CredentialsStorage(tmp_path)
    monkeypatch.setenv("AGENT_API_KEY", "sca_test_token_123")
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.connect(agent_version="9.9.9", protocol_version=1)
    assert out.agent_instance_id == UUID(instance)


@pytest.mark.asyncio
async def test_connect_agent_api_key_401_raises_cloud_auth_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudAuthError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": {"message": "agent token revoked"}})

    storage = CredentialsStorage(tmp_path)
    monkeypatch.setenv("AGENT_API_KEY", "sca_revoked")
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudAuthError, match="agent token revoked"):
        await client.connect(agent_version="1", protocol_version=1)


@pytest.mark.asyncio
async def test_connect_prefers_credentials_json_over_agent_api_key_env(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    instance = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer access-1"
        return httpx.Response(200, json={"agent_instance_id": instance})

    storage = _write_creds(tmp_path)
    monkeypatch.setenv("AGENT_API_KEY", "sca_ignored")
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.connect(agent_version="1", protocol_version=1)
    assert out.agent_instance_id == UUID(instance)


@pytest.mark.asyncio
async def test_connect_raises_when_no_credentials_and_no_agent_api_key(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudConnectionError

    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    storage = CredentialsStorage(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with pytest.raises(CloudConnectionError, match="missing agent_token"):
        await client.connect(agent_version="1", protocol_version=1)


@pytest.mark.asyncio
async def test_connect_403_agent_suspended(tmp_path: Path) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudAgentSuspendedError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"detail": {"code": "agent_suspended", "message": "suspended"}},
        )

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudAgentSuspendedError, match="suspended"):
        await client.connect(agent_version="1", protocol_version=1)


@pytest.mark.asyncio
async def test_ping_403_agent_suspended(tmp_path: Path) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudAgentSuspendedError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"detail": {"code": "agent_suspended", "message": "off"}},
        )

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudAgentSuspendedError):
        await client.ping(
            agent_instance_id=UUID("44444444-4444-4444-8444-444444444444"),
            agent_version="1",
            protocol_version=1,
            openclaw_status="stopped",
            openclaw_error=None,
            command_result=None,
        )


@pytest.mark.asyncio
async def test_connect_wraps_connecterror_as_cloud_connection_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudConnectionError

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=request)

    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudConnectionError, match="simulated"):
        await client.connect(agent_version="1", protocol_version=1)


@pytest.mark.asyncio
async def test_ping_includes_browser_payload_in_json_body(tmp_path: Path) -> None:
    browser = {
        "status": "running",
        "kasmvnc_running": True,
        "chrome_running": True,
        "error": None,
        "pages": [{"url": "https://a.example", "title": "A", "type": "page"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            body = _json_body(request)
            assert body["protocol_version"] == 2
            assert body["browser"] == browser
            return httpx.Response(200, json={"pending_command": None})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.ping(
        agent_instance_id=UUID("77777777-7777-4777-8777-777777777777"),
        agent_version="1",
        protocol_version=2,
        openclaw_status="running",
        openclaw_error=None,
        command_result=None,
        browser=browser,
    )
    assert out.pending_command is None


@pytest.mark.asyncio
async def test_ping_includes_config_version_in_json_body(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            body = _json_body(request)
            assert body["config_version"] == 42
            return httpx.Response(200, json={"pending_command": None})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.ping(
        agent_instance_id=UUID("77777777-7777-4777-8777-777777777777"),
        agent_version="1",
        protocol_version=2,
        openclaw_status="running",
        openclaw_error=None,
        command_result=None,
        config_version=42,
    )
    assert out.pending_command is None


@pytest.mark.asyncio
async def test_ping_omits_config_version_when_none(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            assert "config_version" not in _json_body(request)
            return httpx.Response(200, json={"pending_command": None})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.ping(
        agent_instance_id=UUID("77777777-7777-4777-8777-777777777777"),
        agent_version="1",
        protocol_version=2,
        openclaw_status="running",
        openclaw_error=None,
        command_result=None,
    )
    assert out.pending_command is None


@pytest.mark.asyncio
async def test_ping_includes_agent_activity_payload_in_json_body(tmp_path: Path) -> None:
    activity = {
        "state": "working",
        "last_event_at": "2026-05-21T12:00:00+00:00",
        "idle_seconds": 3.2,
        "sessions_total": 2,
        "sessions_active": 1,
        "agents": {"supervisor": 1},
        "latest": {
            "agent_id": "supervisor",
            "session_key": "s1",
            "type": "tool_call",
            "tool": "exec",
            "age_seconds": 3.2,
        },
        "recent_errors": [],
        "error": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            body = _json_body(request)
            assert body["agent_activity"] == activity
            return httpx.Response(200, json={"pending_command": None})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.ping(
        agent_instance_id=UUID("77777777-7777-4777-8777-777777777777"),
        agent_version="1",
        protocol_version=2,
        openclaw_status="running",
        openclaw_error=None,
        command_result=None,
        agent_activity=activity,
    )
    assert out.pending_command is None


@pytest.mark.asyncio
async def test_ping_omits_agent_activity_when_not_provided(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/connection/ping":
            body = _json_body(request)
            assert "agent_activity" not in body
            return httpx.Response(200, json={"pending_command": None})
        return httpx.Response(404)

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.ping(
        agent_instance_id=UUID("77777777-7777-4777-8777-777777777777"),
        agent_version="1",
        protocol_version=2,
        openclaw_status="running",
        openclaw_error=None,
        command_result=None,
    )
    assert out.pending_command is None


def _client(tmp_path: Path, handler: object) -> SellerClawConnectionClient:
    return SellerClawConnectionClient(
        credentials_storage=_write_creds(tmp_path),
        base_url="http://example",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


_WA_LOGIN_ID = "33333333-3333-4333-8333-333333333333"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        pytest.param(
            httpx.Response(200, json={"login_id": _WA_LOGIN_ID, "force": True}),
            (UUID(_WA_LOGIN_ID), True),
            id="open-attempt",
        ),
        pytest.param(httpx.Response(404, json={"detail": {"code": "whatsapp_link_not_found"}}), None, id="none"),
    ],
)
async def test_fetch_whatsapp_link(tmp_path: Path, response: httpx.Response, expected: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/agent/connection/whatsapp/link"
        assert request.headers.get("authorization") == "Bearer access-1"
        return response

    out = await _client(tmp_path, handler).fetch_whatsapp_link()
    assert (None if out is None else (out.login_id, out.force)) == expected


@pytest.mark.parametrize(
    ("status", "accepted"),
    [
        pytest.param(204, True, id="accepted"),
        pytest.param(404, False, id="attempt-closed"),
    ],
)
async def test_post_whatsapp_link_state(tmp_path: Path, status: int, accepted: bool) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = _json_body(request)
        return httpx.Response(status)

    state = {"status": "qr", "qr_data_url": "data:image/png;base64,AAAA", "reason": None, "message": None, "self_e164": None}
    out = await _client(tmp_path, handler).post_whatsapp_link_state(login_id=UUID(_WA_LOGIN_ID), state=state)
    assert out is accepted
    assert seen == {"path": f"/agent/connection/whatsapp/link/{_WA_LOGIN_ID}", "body": state}


async def test_post_whatsapp_link_state_raises_on_server_error(tmp_path: Path) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudConnectionError

    client = _client(tmp_path, lambda _request: httpx.Response(503))
    with pytest.raises(CloudConnectionError, match="HTTP 503"):
        await client.post_whatsapp_link_state(login_id=UUID(_WA_LOGIN_ID), state={"status": "qr"})


async def test_whatsapp_session_round_trip(tmp_path: Path) -> None:
    stored: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/whatsapp/session"
        if request.method == "POST":
            assert request.headers.get("content-type") == "application/gzip"
            stored["archive"] = request.content
            return httpx.Response(204)
        if request.method == "GET":
            if "archive" not in stored:
                return httpx.Response(404, json={"detail": {"code": "whatsapp_session_not_found"}})
            return httpx.Response(200, content=stored["archive"])
        assert request.method == "DELETE"
        stored.pop("archive", None)
        return httpx.Response(204)

    client = _client(tmp_path, handler)
    assert await client.download_whatsapp_session() is None
    await client.upload_whatsapp_session(b"\x1f\x8bsession")
    assert await client.download_whatsapp_session() == b"\x1f\x8bsession"
    await client.delete_whatsapp_session()
    assert await client.download_whatsapp_session() is None


async def test_ping_sends_the_whatsapp_state_only_when_known(tmp_path: Path) -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(_json_body(request))
        return httpx.Response(200, json={"pending_command": None})

    client = _client(tmp_path, handler)
    common = {
        "agent_instance_id": UUID("22222222-2222-4222-8222-222222222222"),
        "agent_version": "1.0.0",
        "protocol_version": 5,
        "openclaw_status": "running",
        "openclaw_error": None,
        "command_result": None,
    }
    whatsapp = {"linked": True, "connected": True, "self_e164": "+15550001111", "logged_out": False, "observed_at": "2026-10-14T12:00:00+00:00"}
    await client.ping(**common)
    await client.ping(**common, whatsapp=whatsapp)
    assert "whatsapp" not in bodies[0]
    assert bodies[1]["whatsapp"] == whatsapp
