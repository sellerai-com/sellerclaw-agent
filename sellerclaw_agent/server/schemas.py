from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ManifestModelSpec(BaseModel):
    """LiteLLM logical model group (matches YAML `models.complex` / `models.simple`)."""

    id: str
    name: str
    reasoning: bool = False
    input: list[str] | str = Field(default_factory=lambda: ["text"])
    context_window: int
    max_tokens: int


class ManifestModels(BaseModel):
    complex: ManifestModelSpec
    simple: ManifestModelSpec


class ManifestTelegram(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    allowed_user_ids: list[str] = Field(default_factory=list)
    allowed_group_ids: list[str] = Field(default_factory=list)


class ManifestWebSearch(BaseModel):
    enabled: bool = False
    provider: str | None = None
    api_key: str = ""


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
    """Request body mirroring `bundle_manifest_from_mapping` input."""

    user_id: UUID
    gateway_token: str
    hooks_token: str
    litellm_base_url: str
    litellm_api_key: str
    models: ManifestModels
    webhook_api_base_url: str
    template_variables: dict[str, str] = Field(default_factory=dict)
    enabled_modules: list[str] = Field(default_factory=list)
    connected_integrations: list[str] = Field(default_factory=list)
    global_browser_enabled: bool = True
    per_module_browser: dict[str, bool] = Field(default_factory=dict)
    telegram: ManifestTelegram = Field(default_factory=ManifestTelegram)
    web_search: ManifestWebSearch = Field(default_factory=ManifestWebSearch)
    primary_channel: str = "sellerclaw-ui"
    proxy_url: str = ""
    model_name_prefix: str = ""
    extra_allowed_origins: list[str] = Field(default_factory=list)

    def _model_spec_mapping(self, spec: ManifestModelSpec) -> dict[str, Any]:
        inp: list[str] | str
        if isinstance(spec.input, str):
            inp = spec.input
        else:
            inp = list(spec.input)
        return {
            "id": spec.id,
            "name": spec.name,
            "reasoning": spec.reasoning,
            "input": inp,
            "context_window": spec.context_window,
            "max_tokens": spec.max_tokens,
        }

    def to_mapping(self) -> dict[str, object]:
        """Plain dict for `bundle_manifest_from_mapping` / JSON persistence."""
        return {
            "user_id": str(self.user_id),
            "gateway_token": self.gateway_token,
            "hooks_token": self.hooks_token,
            "litellm_base_url": self.litellm_base_url,
            "litellm_api_key": self.litellm_api_key,
            "models": {
                "complex": self._model_spec_mapping(self.models.complex),
                "simple": self._model_spec_mapping(self.models.simple),
            },
            "webhook_api_base_url": self.webhook_api_base_url,
            "template_variables": dict(self.template_variables),
            "enabled_modules": list(self.enabled_modules),
            "connected_integrations": list(self.connected_integrations),
            "global_browser_enabled": self.global_browser_enabled,
            "per_module_browser": dict(self.per_module_browser),
            "telegram": {
                "enabled": self.telegram.enabled,
                "bot_token": self.telegram.bot_token,
                "allowed_user_ids": list(self.telegram.allowed_user_ids),
                "allowed_group_ids": list(self.telegram.allowed_group_ids),
            },
            "web_search": {
                "enabled": self.web_search.enabled,
                "provider": self.web_search.provider,
                "api_key": self.web_search.api_key,
            },
            "primary_channel": self.primary_channel,
            "proxy_url": self.proxy_url,
            "model_name_prefix": self.model_name_prefix,
            "extra_allowed_origins": list(self.extra_allowed_origins),
        }
