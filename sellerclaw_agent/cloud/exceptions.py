from __future__ import annotations

from typing import Any


def agent_api_error_code(payload: Any) -> str | None:
    """Extract ``detail.code`` from a SellerClaw error body, if present."""
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str):
            return code
    return None


def is_agent_suspended_api_payload(payload: Any) -> bool:
    """True when SellerClaw error body marks the edge agent as suspended (403)."""
    return agent_api_error_code(payload) == "agent_suspended"


class CloudAuthError(Exception):
    """SellerClaw rejected credentials or returned an auth-related client error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CloudConnectionError(Exception):
    """Network failure, timeout, or invalid response when calling SellerClaw."""


class CloudAgentSuspendedError(CloudConnectionError):
    """Server returned 403 agent_suspended; wait for user resume before connecting."""


class CloudSessionInvalidatedError(CloudAuthError):
    """Server rejected the request because the local ``agent_instance_id`` is stale.

    Recovery is session-scoped: clear ``edge_session.json`` only and let the ping
    loop ``connect()`` a fresh session. Do NOT wipe the ``sca_`` agent token —
    it is still valid, only the session id is no longer recognised by cloud.
    """


class CloudConnectionInactiveError(CloudConnectionError):
    """Server has no CONNECTED edge session for this user (ping loop will restore it)."""


class CloudDevicePollTerminalError(CloudConnectionError):
    """Device code was already redeemed or is no longer valid; not a transient server error."""
