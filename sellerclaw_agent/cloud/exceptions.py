from __future__ import annotations

from typing import Any, ClassVar


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


class CloudConnectionAlreadyActiveError(CloudConnectionError):
    """``connect`` refused (409 ``agent_connection_already_active``): another instance holds the session.

    The one signal that this agent was replaced (or is a second copy): the cloud only lets a new
    session in once the previous one is gone or stale, so a refusal means somebody else is the
    owner's agent right now. The cron sync goes on standby on it (see ``cron_standby``).

    ``code`` is the cloud's ``detail.code``; the subclasses below are the refusals of protocol 10,
    all handled the same way (wait, retry), and the code reaches ``/health`` so the CLI can tell
    the owner what is going on.
    """

    code: ClassVar[str] = "agent_connection_already_active"


class CloudAgentMoveInProgressError(CloudConnectionAlreadyActiveError):
    """409 ``agent_move_in_progress``: the owner is moving the agent here; the cloud machine is
    still handing over. The next ``connect`` after it has gone is accepted."""

    code = "agent_move_in_progress"


class CloudAgentRunsInCloudError(CloudConnectionAlreadyActiveError):
    """409 ``agent_runs_in_cloud``: the owner's agent runs on a SellerClaw cloud machine and no
    move to this computer was asked for (``sellerclaw-agent login`` offers it)."""

    code = "agent_runs_in_cloud"


class CloudAgentRunsOnComputerError(CloudConnectionAlreadyActiveError):
    """409 ``agent_runs_on_computer``: this is a cloud machine, but the owner's agent now runs on
    their own computer — an old machine must not take the session back."""

    code = "agent_runs_on_computer"


#: ``detail.code`` of a refused ``connect`` → the exception raised for it.
CONNECT_REFUSAL_ERRORS: dict[str, type[CloudConnectionAlreadyActiveError]] = {
    cls.code: cls
    for cls in (
        CloudConnectionAlreadyActiveError,
        CloudAgentMoveInProgressError,
        CloudAgentRunsInCloudError,
        CloudAgentRunsOnComputerError,
    )
}


class CloudMoveRefusedError(CloudConnectionError):
    """``POST /agent/hosting/move-here`` refused (4xx); ``code`` / ``message`` come from the cloud."""

    def __init__(self, message: str, *, code: str | None, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class CloudDevicePollTerminalError(CloudConnectionError):
    """Device code was already redeemed or is no longer valid; not a transient server error."""
