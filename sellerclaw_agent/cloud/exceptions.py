from __future__ import annotations

from typing import Any


def is_agent_suspended_api_payload(payload: Any) -> bool:
    """True when SellerClaw error body marks the edge agent as suspended (403)."""
    if not isinstance(payload, dict):
        return False
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return detail.get("code") == "agent_suspended"
    return False


class CloudAuthError(Exception):
    """SellerClaw rejected credentials or returned an auth-related client error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CloudConnectionError(Exception):
    """Network failure, timeout, or invalid response when calling SellerClaw."""


class CloudAgentSuspendedError(CloudConnectionError):
    """Server returned 403 agent_suspended; wait for user resume before connecting."""


class CloudDevicePollTerminalError(CloudConnectionError):
    """Device code was already redeemed or is no longer valid; not a transient server error."""
