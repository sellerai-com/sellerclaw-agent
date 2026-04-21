from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sellerclaw_agent.cloud.auth_client import AgentAuthResult, DeviceCodeResult, DeviceTokenPollResult
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudDevicePollTerminalError
from sellerclaw_agent.cloud.service import CloudAuthService

pytestmark = pytest.mark.unit

_UID = UUID("35922ddf-4020-5179-b163-3d90bcb86b00")
_INST = UUID("22222222-2222-4222-8222-222222222222")


class _FakeAuthClient:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def login(self, *, email: str, password: str) -> AgentAuthResult:
        if self._fail:
            raise CloudAuthError("Invalid credentials", status_code=400)
        return AgentAuthResult(
            agent_token="sca_fake",
            user_id=_UID,
            user_email=email,
            user_name="Alice",
        )

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
        return DeviceTokenPollResult(pending=True, error=None, auth=None)


@pytest.mark.asyncio
async def test_connect_persists_and_returns_status(tmp_path: Path) -> None:
    session = EdgeSessionStorage(tmp_path)
    svc = CloudAuthService(
        auth_client=_FakeAuthClient(),
        credentials_storage=CredentialsStorage(tmp_path),
        session_storage=session,
    )
    status = await svc.connect(email="a@b.c", password="p")
    assert status.connected is True
    assert status.user_id == _UID
    assert status.user_email == "a@b.c"
    assert status.connected_at is not None

    stored = svc.credentials_storage.load()
    assert stored is not None
    assert stored.agent_token == "sca_fake"


@pytest.mark.asyncio
async def test_connect_raises_on_auth_failure(tmp_path: Path) -> None:
    svc = CloudAuthService(
        auth_client=_FakeAuthClient(fail=True),
        credentials_storage=CredentialsStorage(tmp_path),
        session_storage=EdgeSessionStorage(tmp_path),
    )
    with pytest.raises(CloudAuthError, match="Invalid credentials"):
        await svc.connect(email="a@b.c", password="wrong")


def test_get_status_disconnected(tmp_path: Path) -> None:
    svc = CloudAuthService(
        auth_client=_FakeAuthClient(),
        credentials_storage=CredentialsStorage(tmp_path),
        session_storage=EdgeSessionStorage(tmp_path),
    )
    assert svc.get_status().connected is False


@pytest.mark.asyncio
async def test_poll_device_flow_raises_terminal_when_cloud_returns_authorization_invalid(
    tmp_path: Path,
) -> None:
    class _Terminal(_FakeAuthClient):
        async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
            _ = device_code
            return DeviceTokenPollResult(pending=False, error="authorization_invalid", auth=None)

    svc = CloudAuthService(
        auth_client=_Terminal(),
        credentials_storage=CredentialsStorage(tmp_path),
        session_storage=EdgeSessionStorage(tmp_path),
    )
    with pytest.raises(CloudDevicePollTerminalError, match="authorization_invalid"):
        await svc.poll_device_flow(device_code="dc")


@pytest.mark.asyncio
async def test_disconnect_calls_cloud_and_clears_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_instance = MagicMock()
    mock_instance.disconnect = AsyncMock()
    mock_cls = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("sellerclaw_agent.cloud.service.SellerClawConnectionClient", mock_cls)

    session = EdgeSessionStorage(tmp_path)
    session.save(agent_instance_id=_INST, protocol_version=1)
    svc = CloudAuthService(
        auth_client=_FakeAuthClient(),
        credentials_storage=CredentialsStorage(tmp_path),
        session_storage=session,
    )
    await svc.connect(email="a@b.c", password="p")
    assert svc.get_status().connected is True
    assert session.load() is not None

    await svc.disconnect()
    assert svc.get_status().connected is False
    assert session.load() is None
    mock_instance.disconnect.assert_awaited_once()
