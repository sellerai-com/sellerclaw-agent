from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest
from sellerclaw_agent.cloud.auth_client import DeviceTokenPollResult, SellerClawAuthClient
from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudConnectionError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_login_success() -> None:
    uid = "35922ddf-4020-5179-b163-3d90bcb86b00"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/auth/login"
        body = json_bytes(request)
        assert body["email"] == "u@example.com"
        assert body["password"] == "secret"
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "user": {"id": uid, "email": "u@example.com", "name": "User"},
            },
        )

    client = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    result = await client.login(email="u@example.com", password="secret")
    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    assert result.user_id == UUID(uid)
    assert result.user_email == "u@example.com"
    assert result.user_name == "User"


@pytest.mark.asyncio
async def test_login_invalid_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error_code": "authentication_failed", "detail": "Invalid credentials"},
        )

    client = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudAuthError, match="Invalid credentials"):
        await client.login(email="u@example.com", password="wrong")


@pytest.mark.asyncio
async def test_login_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    client = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudConnectionError, match="server error"):
        await client.login(email="u@example.com", password="secret")


@pytest.mark.asyncio
async def test_refresh_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/refresh"
        body = json_bytes(request)
        assert body["refresh_token"] == "old-refresh"
        return httpx.Response(200, json={"access_token": "new-access"})

    client = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    token = await client.refresh(refresh_token="old-refresh")
    assert token == "new-access"


@pytest.mark.asyncio
async def test_refresh_invalid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "Not a refresh token"})

    client = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudAuthError, match="Not a refresh token"):
        await client.refresh(refresh_token="bad")


def json_bytes(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


@pytest.mark.asyncio
async def test_request_device_code_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/auth/device/code"
        return httpx.Response(
            200,
            json={
                "device_code": "devc",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://app/a?c=1",
                "expires_in": 900,
                "interval": 5,
            },
        )

    client = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.request_device_code()
    assert out.device_code == "devc"
    assert out.user_code == "ABCD-EFGH"
    assert out.expires_in == 900


@pytest.mark.asyncio
async def test_poll_device_token_pending_and_success() -> None:
    uid = "35922ddf-4020-5179-b163-3d90bcb86b00"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/device/token"
        body = json_bytes(request)
        calls.append(body["device_code"])
        if len(calls) == 1:
            return httpx.Response(200, json={"error": "authorization_pending"})
        return httpx.Response(
            200,
            json={
                "access_token": "a",
                "refresh_token": "r",
                "user": {"id": uid, "email": "u@e.com", "name": "U"},
            },
        )

    client = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    p1 = await client.poll_device_token(device_code="dc")
    assert p1 == DeviceTokenPollResult(pending=True, error=None, login=None)
    p2 = await client.poll_device_token(device_code="dc")
    assert p2.pending is False
    assert p2.login is not None
    assert p2.login.access_token == "a"


@pytest.mark.asyncio
async def test_poll_device_token_authorization_invalid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, json={"error": "authorization_invalid"})

    client = SellerClawAuthClient(
        base_url="http://example",
        transport=httpx.MockTransport(handler),
    )
    out = await client.poll_device_token(device_code="dc")
    assert out == DeviceTokenPollResult(pending=False, error="authorization_invalid", login=None)
