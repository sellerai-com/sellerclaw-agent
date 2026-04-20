from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pytest import MonkeyPatch
from sellerclaw_agent.cloud.auth_client import SellerClawAuthClient
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
        access_token="access-1",
        refresh_token="refresh-1",
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
    noop_auth = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        auth_client=noop_auth,
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.connect(agent_version="9.9.9", protocol_version=1)
    assert out.agent_instance_id == UUID(instance)


@pytest.mark.asyncio
async def test_ping_refresh_on_401_then_success(tmp_path: Path) -> None:
    ping_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            body = _json_body(request)
            assert body["refresh_token"] == "refresh-1"
            return httpx.Response(200, json={"access_token": "access-2"})
        if request.url.path == "/agent/connection/ping":
            ping_count["n"] += 1
            if ping_count["n"] == 1:
                return httpx.Response(401, json={"detail": {"message": "expired"}})
            assert request.headers.get("authorization") == "Bearer access-2"
            return httpx.Response(
                200,
                json={
                    "pending_command": {
                        "command_id": "33333333-3333-4333-8333-333333333333",
                        "command_type": "stop",
                        "issued_at": "2026-04-15T12:00:00Z",
                    },
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    storage = _write_creds(tmp_path)
    auth = SellerClawAuthClient(base_url="http://example", transport=transport)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        auth_client=auth,
        base_url="http://example",
        transport=transport,
    )
    out = await client.ping(
        agent_instance_id=UUID("44444444-4444-4444-8444-444444444444"),
        agent_version="1",
        protocol_version=1,
        openclaw_status="stopped",
        openclaw_error=None,
        command_result=None,
    )
    assert out.pending_command is not None
    assert out.pending_command.command_type == "stop"
    reloaded = storage.load()
    assert reloaded is not None
    assert reloaded.access_token == "access-2"


@pytest.mark.asyncio
async def test_fetch_edge_manifest_success(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/edge-manifest"
        assert request.headers.get("authorization") == "Bearer access-1"
        return httpx.Response(200, json={"user_id": "11111111-1111-4111-8111-111111111111", "gateway_token": "g"})

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        auth_client=SellerClawAuthClient(
            base_url="http://example",
            transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
        ),
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    data = await client.fetch_edge_manifest()
    assert data["gateway_token"] == "g"


def _noop_auth() -> SellerClawAuthClient:
    return SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )


@pytest.mark.asyncio
async def test_disconnect_success(tmp_path: Path) -> None:
    instance = "55555555-5555-4555-8555-555555555555"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/connection/disconnect"
        body = _json_body(request)
        assert body["agent_instance_id"] == instance
        return httpx.Response(200, json={"status": "ok"})

    storage = _write_creds(tmp_path)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        auth_client=_noop_auth(),
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    await client.disconnect(agent_instance_id=UUID(instance))


@pytest.mark.asyncio
async def test_connect_401_raises_cloud_auth_error(tmp_path: Path) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudAuthError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            return httpx.Response(200, json={"access_token": "access-new"})
        return httpx.Response(401, json={"detail": {"message": "Invalid token"}})

    transport = httpx.MockTransport(handler)
    storage = _write_creds(tmp_path)
    auth = SellerClawAuthClient(base_url="http://example", transport=transport)
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        auth_client=auth,
        base_url="http://example",
        transport=transport,
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
        auth_client=_noop_auth(),
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
        auth_client=_noop_auth(),
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
        auth_client=_noop_auth(),
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
        auth_client=_noop_auth(),
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
    refresh_called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            refresh_called["n"] += 1
            return httpx.Response(500)
        assert request.url.path == "/agent/connection/connect"
        assert request.headers.get("authorization") == "Bearer sca_test_token_123"
        return httpx.Response(200, json={"agent_instance_id": instance})

    storage = CredentialsStorage(tmp_path)
    monkeypatch.setenv("AGENT_API_KEY", "sca_test_token_123")
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        auth_client=SellerClawAuthClient(
            base_url="http://example",
            transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
        ),
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.connect(agent_version="9.9.9", protocol_version=1)
    assert out.agent_instance_id == UUID(instance)
    assert refresh_called["n"] == 0


@pytest.mark.asyncio
async def test_connect_agent_api_key_401_raises_cloud_auth_error_without_refresh(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from sellerclaw_agent.cloud.exceptions import CloudAuthError

    refresh_called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            refresh_called["n"] += 1
            return httpx.Response(200, json={"access_token": "should-not-be-used"})
        return httpx.Response(401, json={"detail": {"message": "agent token revoked"}})

    storage = CredentialsStorage(tmp_path)
    monkeypatch.setenv("AGENT_API_KEY", "sca_revoked")
    client = SellerClawConnectionClient(
        credentials_storage=storage,
        auth_client=SellerClawAuthClient(base_url="http://example", transport=httpx.MockTransport(handler)),
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudAuthError, match="agent token revoked"):
        await client.connect(agent_version="1", protocol_version=1)
    assert refresh_called["n"] == 0


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
        auth_client=_noop_auth(),
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
        auth_client=_noop_auth(),
        base_url="http://example",
        transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with pytest.raises(CloudConnectionError, match="missing credentials"):
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
        auth_client=_noop_auth(),
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
        auth_client=_noop_auth(),
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
