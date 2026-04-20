from __future__ import annotations

from typing import Protocol

from sellerclaw_agent.cloud.auth_client import DeviceCodeResult, DeviceTokenPollResult, LoginResult


class SellerClawAuthClientPort(Protocol):
    """HTTP adapter for SellerClaw public auth endpoints (login / refresh)."""

    async def login(self, *, email: str, password: str) -> LoginResult:
        """Exchange email/password for tokens and user profile."""
        ...

    async def refresh(self, *, refresh_token: str) -> str:
        """Exchange refresh token for a new access token."""
        ...

    async def request_device_code(self) -> DeviceCodeResult:
        """Start OAuth2-style device authorization on SellerClaw."""
        ...

    async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
        """Poll SellerClaw for device authorization result."""
        ...
