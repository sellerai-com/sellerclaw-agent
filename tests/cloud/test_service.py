from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sellerclaw_agent.cloud.auth_client import DeviceCodeResult, DeviceTokenPollResult, LoginResult
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudDevicePollTerminalError
from sellerclaw_agent.cloud.service import CloudAuthService

pytestmark = pytest.mark.unit

_UID = UUID("35922ddf-4020-5179-b163-3d90bcb86b00")


class _FakeAuthClient:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def login(self, *, email: str, password: str) -> LoginResult:
        if self._fail:
            raise CloudAuthError("Invalid credentials", status_code=400)
        return LoginResult(
            access_token="at",
            refresh_token="rt",
            user_id=_UID,
            user_email=email,
            user_name="Alice",
        )

    async def refresh(self, *, refresh_token: str) -> str:
        _ = refresh_token
        return "new-at"

    async def request_device_code(self) -> DeviceCodeResult:
        return DeviceCodeResult(
            device_code="d",
            user_code="ABCD-EFGH",
            verification_uri="https://x/auth/device",
            expires_in=1,
            interval=1,
        )

    async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
        _ = device_code
        return DeviceTokenPollResult(pending=True, error=None, login=None)


@pytest.mark.asyncio
async def test_connect_persists_and_returns_status(tmp_path: Path) -> None:
    svc = CloudAuthService(
        auth_client=_FakeAuthClient(),
        credentials_storage=CredentialsStorage(tmp_path),
    )
    status = await svc.connect(email="a@b.c", password="p")
    assert status.connected is True
    assert status.user_id == _UID
    assert status.user_email == "a@b.c"
    assert status.connected_at is not None

    stored = svc.credentials_storage.load()
    assert stored is not None
    assert stored.access_token == "at"
    assert stored.refresh_token == "rt"


@pytest.mark.asyncio
async def test_connect_raises_on_auth_failure(tmp_path: Path) -> None:
    svc = CloudAuthService(
        auth_client=_FakeAuthClient(fail=True),
        credentials_storage=CredentialsStorage(tmp_path),
    )
    with pytest.raises(CloudAuthError, match="Invalid credentials"):
        await svc.connect(email="a@b.c", password="wrong")


def test_get_status_disconnected(tmp_path: Path) -> None:
    svc = CloudAuthService(
        auth_client=_FakeAuthClient(),
        credentials_storage=CredentialsStorage(tmp_path),
    )
    assert svc.get_status().connected is False


@pytest.mark.asyncio
async def test_poll_device_flow_raises_terminal_when_cloud_returns_authorization_invalid(
    tmp_path: Path,
) -> None:
    class _Terminal(_FakeAuthClient):
        async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
            _ = device_code
            return DeviceTokenPollResult(pending=False, error="authorization_invalid", login=None)

    svc = CloudAuthService(
        auth_client=_Terminal(),
        credentials_storage=CredentialsStorage(tmp_path),
    )
    with pytest.raises(CloudDevicePollTerminalError, match="authorization_invalid"):
        await svc.poll_device_flow(device_code="dc")


@pytest.mark.asyncio
async def test_disconnect_clears_credentials(tmp_path: Path) -> None:
    svc = CloudAuthService(
        auth_client=_FakeAuthClient(),
        credentials_storage=CredentialsStorage(tmp_path),
    )
    await svc.connect(email="a@b.c", password="p")
    assert svc.get_status().connected is True
    svc.disconnect()
    assert svc.get_status().connected is False
