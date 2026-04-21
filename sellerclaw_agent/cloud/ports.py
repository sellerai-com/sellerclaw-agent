from __future__ import annotations

from typing import Protocol

from sellerclaw_agent.cloud.auth_client import AgentAuthResult, DeviceCodeResult, DeviceTokenPollResult


class SellerClawAuthClientPort(Protocol):
    """HTTP adapter for SellerClaw ``/agent/auth/*`` (agent token only)."""

    async def login(self, *, email: str, password: str) -> AgentAuthResult:
        """Exchange email/password for ``sca_`` token and user profile."""
        ...

    async def request_device_code(self) -> DeviceCodeResult:
        """Start device authorization on SellerClaw."""
        ...

    async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
        """Poll SellerClaw for device authorization result."""
        ...
