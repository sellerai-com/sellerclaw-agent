from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sellerclaw_agent.cloud.auth_client import DeviceCodeResult, DeviceTokenPollResult, LoginResult
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudConnectionError
from sellerclaw_agent.cloud.service import CloudAuthService
from sellerclaw_agent.server.app import app, get_cloud_auth_service

pytestmark = pytest.mark.unit


class _FakeAuthClientOk:
    async def login(self, *, email: str, password: str) -> LoginResult:
        _ = email, password
        return LoginResult(
            access_token="access",
            refresh_token="refresh",
            user_id=UUID("35922ddf-4020-5179-b163-3d90bcb86b00"),
            user_email="u@example.com",
            user_name="User",
        )

    async def refresh(self, *, refresh_token: str) -> str:
        _ = refresh_token
        return "new-access"

    async def request_device_code(self) -> DeviceCodeResult:
        return DeviceCodeResult(
            device_code="dc1",
            user_code="ABCD-EFGH",
            verification_uri="https://app.example/auth/device?code=ABCD-EFGH",
            expires_in=900,
            interval=5,
        )

    async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
        _ = device_code
        return DeviceTokenPollResult(pending=True, error=None, login=None)


class _FakeAuthClientAuthError:
    async def login(self, *, email: str, password: str) -> LoginResult:
        _ = email, password
        raise CloudAuthError("Invalid credentials", status_code=400)

    async def refresh(self, *, refresh_token: str) -> str:
        _ = refresh_token
        return "x"

    async def request_device_code(self) -> DeviceCodeResult:
        raise CloudAuthError("nope", status_code=400)

    async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
        _ = device_code
        raise CloudAuthError("nope", status_code=400)


class _FakeAuthClientConnectionError:
    async def login(self, *, email: str, password: str) -> LoginResult:
        _ = email, password
        raise CloudConnectionError("upstream down")

    async def refresh(self, *, refresh_token: str) -> str:
        _ = refresh_token
        return "x"

    async def request_device_code(self) -> DeviceCodeResult:
        raise CloudConnectionError("upstream down")

    async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
        _ = device_code
        raise CloudConnectionError("upstream down")


@pytest.fixture
def _override_service_ok(tmp_path: Path) -> Generator[None, None, None]:
    def _factory() -> CloudAuthService:
        return CloudAuthService(
            auth_client=_FakeAuthClientOk(),
            credentials_storage=CredentialsStorage(tmp_path),
        )

    app.dependency_overrides[get_cloud_auth_service] = _factory
    yield
    app.dependency_overrides.clear()


@pytest.mark.usefixtures("_override_service_ok")
def test_auth_connect_status_disconnect() -> None:
    with TestClient(app) as client:
        r = client.post("/auth/connect", json={"email": "u@example.com", "password": "secret"})
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["user_email"] == "u@example.com"
        assert body["user_id"] == "35922ddf-4020-5179-b163-3d90bcb86b00"

        s = client.get("/auth/status")
        assert s.status_code == 200
        assert s.json()["connected"] is True

        d = client.post("/auth/disconnect")
        assert d.status_code == 200
        assert d.json()["status"] == "ok"

        s2 = client.get("/auth/status")
        assert s2.json()["connected"] is False


def test_auth_connect_returns_401_on_cloud_auth_error(tmp_path: Path) -> None:
    def _factory() -> CloudAuthService:
        return CloudAuthService(
            auth_client=_FakeAuthClientAuthError(),
            credentials_storage=CredentialsStorage(tmp_path),
        )

    app.dependency_overrides[get_cloud_auth_service] = _factory
    try:
        with TestClient(app) as client:
            r = client.post("/auth/connect", json={"email": "u@example.com", "password": "bad"})
            assert r.status_code == 401
            assert "Invalid credentials" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_auth_connect_returns_502_on_cloud_connection_error(tmp_path: Path) -> None:
    def _factory() -> CloudAuthService:
        return CloudAuthService(
            auth_client=_FakeAuthClientConnectionError(),
            credentials_storage=CredentialsStorage(tmp_path),
        )

    app.dependency_overrides[get_cloud_auth_service] = _factory
    try:
        with TestClient(app) as client:
            r = client.post("/auth/connect", json={"email": "u@example.com", "password": "x"})
            assert r.status_code == 502
            assert "upstream down" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.usefixtures("_override_service_ok")
def test_auth_device_start_returns_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_VERIFICATION_BASE_URL", "http://my-admin:5174")
    with TestClient(app) as client:
        r = client.post("/auth/device/start")
        assert r.status_code == 200
        body = r.json()
        assert body["device_code"] == "dc1"
        assert body["user_code"] == "ABCD-EFGH"
        assert body["verification_uri"] == "http://my-admin:5174/auth/device?code=ABCD-EFGH"


def test_auth_device_poll_pending_then_completed(tmp_path: Path) -> None:
    uid = UUID("35922ddf-4020-5179-b163-3d90bcb86b00")

    class _PollFake(_FakeAuthClientOk):
        def __init__(self) -> None:
            self._n = 0

        async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
            _ = device_code
            self._n += 1
            if self._n == 1:
                return DeviceTokenPollResult(pending=True, error=None, login=None)
            return DeviceTokenPollResult(
                pending=False,
                error=None,
                login=LoginResult(
                    access_token="a",
                    refresh_token="r",
                    user_id=uid,
                    user_email="u@example.com",
                    user_name="User",
                ),
            )

    poll_fake = _PollFake()

    def _factory() -> CloudAuthService:
        return CloudAuthService(
            auth_client=poll_fake,
            credentials_storage=CredentialsStorage(tmp_path),
        )

    app.dependency_overrides[get_cloud_auth_service] = _factory
    try:
        with TestClient(app) as client:
            p1 = client.get("/auth/device/poll", params={"device_code": "dc"})
            assert p1.status_code == 200
            assert p1.json()["status"] == "pending"

            p2 = client.get("/auth/device/poll", params={"device_code": "dc"})
            assert p2.status_code == 200
            assert p2.json()["status"] == "completed"
            assert p2.json()["auth"]["connected"] is True
            assert p2.json()["auth"]["user_email"] == "u@example.com"
    finally:
        app.dependency_overrides.clear()


def test_auth_device_poll_returns_400_on_authorization_invalid(tmp_path: Path) -> None:
    class _Terminal(_FakeAuthClientOk):
        async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
            _ = device_code
            return DeviceTokenPollResult(pending=False, error="authorization_invalid", login=None)

    def _factory() -> CloudAuthService:
        return CloudAuthService(
            auth_client=_Terminal(),
            credentials_storage=CredentialsStorage(tmp_path),
        )

    app.dependency_overrides[get_cloud_auth_service] = _factory
    try:
        with TestClient(app) as client:
            r = client.get("/auth/device/poll", params={"device_code": "x"})
            assert r.status_code == 400
            assert "authorization_invalid" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_auth_device_poll_returns_502_on_cloud_error(tmp_path: Path) -> None:
    class _Err(_FakeAuthClientOk):
        async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
            _ = device_code
            return DeviceTokenPollResult(pending=False, error="invalid_device_code", login=None)

    def _factory() -> CloudAuthService:
        return CloudAuthService(
            auth_client=_Err(),
            credentials_storage=CredentialsStorage(tmp_path),
        )

    app.dependency_overrides[get_cloud_auth_service] = _factory
    try:
        with TestClient(app) as client:
            r = client.get("/auth/device/poll", params={"device_code": "x"})
            assert r.status_code == 502
            assert "invalid_device_code" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()
