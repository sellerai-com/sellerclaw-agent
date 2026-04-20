from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sellerclaw_agent.cloud.auth_client import DeviceCodeResult
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import CloudConnectionError, CloudDevicePollTerminalError
from sellerclaw_agent.cloud.ports import SellerClawAuthClientPort


@dataclass(frozen=True)
class AuthStatus:
    connected: bool
    user_id: UUID | None = None
    user_email: str | None = None
    user_name: str | None = None
    connected_at: str | None = None


@dataclass
class CloudAuthService:
    """Orchestrates login/status/disconnect using the auth client and local storage."""

    auth_client: SellerClawAuthClientPort
    credentials_storage: CredentialsStorage

    async def connect(self, *, email: str, password: str) -> AuthStatus:
        """Authenticate against the SellerClaw server and persist the tokens."""
        result = await self.auth_client.login(email=email, password=password)
        connected_at = (
            datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        self.credentials_storage.save(
            user_id=result.user_id,
            user_email=result.user_email,
            user_name=result.user_name,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            connected_at=connected_at,
        )
        return AuthStatus(
            connected=True,
            user_id=result.user_id,
            user_email=result.user_email,
            user_name=result.user_name,
            connected_at=connected_at,
        )

    async def start_device_flow(self) -> DeviceCodeResult:
        """Request a device code from SellerClaw (browser verification)."""
        return await self.auth_client.request_device_code()

    async def poll_device_flow(self, *, device_code: str) -> AuthStatus | None:
        """Poll SellerClaw; persist credentials when approved. None while pending."""
        out = await self.auth_client.poll_device_token(device_code=device_code)
        if out.pending or out.error == "authorization_pending":
            return None
        if out.error == "authorization_invalid":
            raise CloudDevicePollTerminalError(
                "Device authorization failed: authorization_invalid",
            ) from None
        if out.error:
            raise CloudConnectionError(f"Device authorization failed: {out.error}")
        if out.login is None:
            raise CloudConnectionError("Device authorization failed: empty result")
        result = out.login
        connected_at = (
            datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        self.credentials_storage.save(
            user_id=result.user_id,
            user_email=result.user_email,
            user_name=result.user_name,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            connected_at=connected_at,
        )
        return AuthStatus(
            connected=True,
            user_id=result.user_id,
            user_email=result.user_email,
            user_name=result.user_name,
            connected_at=connected_at,
        )

    def get_status(self) -> AuthStatus:
        stored = self.credentials_storage.load()
        if stored is None:
            return AuthStatus(connected=False)
        return AuthStatus(
            connected=True,
            user_id=stored.user_id,
            user_email=stored.user_email,
            user_name=stored.user_name,
            connected_at=stored.connected_at,
        )

    def disconnect(self) -> None:
        self.credentials_storage.clear()
