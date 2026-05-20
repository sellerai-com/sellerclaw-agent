from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ManifestWebSearch(BaseModel):
    """Monolith computes BYOK vs corporate; the manifest only toggles tool availability."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False


class GetManifestResponse(BaseModel):
    """Response body for ``GET /manifest``."""

    manifest: dict[str, Any]
    version: str


class ConnectRequest(BaseModel):
    """Credentials for SellerClaw ``POST /auth/login``."""

    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AuthStatusResponse(BaseModel):
    """Whether the agent has stored cloud credentials (tokens are never exposed)."""

    connected: bool
    user_id: UUID | None = None
    user_email: str | None = None
    user_name: str | None = None
    connected_at: str | None = None


class DeviceStartResponse(BaseModel):
    """Device authorization session started (SellerClaw /auth/device/code)."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class DevicePollResponse(BaseModel):
    """Poll result for device authorization."""

    status: Literal["pending", "completed"]
    auth: AuthStatusResponse | None = None


class DisconnectResponse(BaseModel):
    status: str


class CommandHistoryEntry(BaseModel):
    """One command received by the agent from SellerClaw."""

    command_id: str
    command_type: str
    issued_at: str
    received_at: str
    executed_at: str | None = None
    outcome: str | None = None
    error: str | None = None


class CommandHistoryResponse(BaseModel):
    entries: list[CommandHistoryEntry]


class OpenClawStatusResponse(BaseModel):
    """Runtime status for the OpenClaw gateway (supervisord / legacy API shape)."""

    status: str
    container_name: str | None = None
    container_id: str | None = None
    image: str | None = None
    uptime_seconds: float | None = None
    ports: dict[str, int] | None = None
    error: str | None = None


class OpenClawCommandResponse(BaseModel):
    """Result of ``POST /openclaw/start|stop|restart`` (non-rejected)."""

    outcome: str
    error: str | None = None


class SaveManifestResponse(BaseModel):
    """Response body for ``POST /manifest``."""

    status: str
    manifest_path: str
    version: str


class SaveManifestRequest(BaseModel):
    """Request body for the generic v2 manifest (``POST /manifest``).

    Nested ``llm`` / ``agents`` / ``channels`` blocks are kept loosely typed and
    validated downstream by :func:`bundle_manifest_from_mapping`; this schema only
    enforces the top-level required fields (``user_id``, ``llm``, ``agents``).
    """

    model_config = ConfigDict(extra="ignore")

    user_id: UUID
    agent_api_base_path: str = ""
    proxy_url: str = ""
    web_search: ManifestWebSearch = Field(default_factory=ManifestWebSearch)
    llm: dict[str, Any]
    agents: dict[str, Any]
    channels: dict[str, Any] = Field(default_factory=dict)

    def to_mapping(self) -> dict[str, object]:
        """Plain dict for `bundle_manifest_from_mapping` / JSON persistence."""
        return {
            "user_id": str(self.user_id),
            "agent_api_base_path": self.agent_api_base_path,
            "proxy_url": self.proxy_url,
            "web_search": {"enabled": self.web_search.enabled},
            "llm": self.llm,
            "agents": self.agents,
            "channels": self.channels,
        }
