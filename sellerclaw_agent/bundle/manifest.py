from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import yaml

from sellerclaw_agent.models import AgentModuleId, IntegrationKind

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env_in_str(value: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return os.environ.get(key, "")

    return _ENV_PATTERN.sub(_repl, value)


def _expand_env_recursive(obj: object) -> object:
    if isinstance(obj, str):
        return _expand_env_in_str(obj)
    if isinstance(obj, dict):
        return {str(k): _expand_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_recursive(item) for item in obj]
    return obj


@dataclass(frozen=True)
class TelegramManifest:
    enabled: bool = False
    bot_token: str = ""
    allowed_user_ids: tuple[str, ...] = ()
    allowed_group_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class WebSearchManifest:
    """Web search is configured only on the monolith; the manifest carries a single toggle."""

    enabled: bool = False


@dataclass(frozen=True)
class MediaModelManifest:
    """Reference to an image/video model the agent should expose via openclaw.json.

    The agent emits a matching entry under ``models.providers.litellm.models`` and
    wires the OpenClaw default through ``agents.defaults.{image,video}GenerationModel``.
    LiteLLM provider routing for the alias is configured operator-side.
    """

    model_id: str = ""
    display_name: str = ""
    openclaw_alias: str = ""
    litellm_route: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.openclaw_alias.strip())


@dataclass(frozen=True)
class BundleManifest:
    """Flat input for bundle generation (caller supplies all template data and secrets)."""

    user_id: UUID
    litellm_base_url: str
    litellm_api_key: str
    template_variables: dict[str, str]
    enabled_module_ids: tuple[str, ...] = ()
    connected_integrations: frozenset[IntegrationKind] = field(default_factory=frozenset)
    global_browser_enabled: bool = True
    per_module_browser: dict[str, bool] = field(default_factory=dict)
    telegram: TelegramManifest = field(default_factory=TelegramManifest)
    web_search: WebSearchManifest = field(default_factory=WebSearchManifest)
    primary_channel: str = "sellerclaw-ui"
    proxy_url: str = ""
    model_name_prefix: str = ""
    # Path segment appended to SELLERCLAW_API_URL to form SELLERCLAW_AGENT_API_BASE_URL.
    # Empty string means the agent API lives directly at SELLERCLAW_API_URL.
    agent_api_base_path: str = ""
    image_model: MediaModelManifest = field(default_factory=MediaModelManifest)
    video_model: MediaModelManifest = field(default_factory=MediaModelManifest)
    # After-primary fallbacks for OpenClaw's `{image,video}GenerationModel.fallbacks`.
    # Empty when no further reachable media model exists for this user. The agent must
    # also register each entry under `models.providers.litellm.models` so OpenClaw can
    # resolve the alias.
    image_fallbacks: tuple[MediaModelManifest, ...] = ()
    video_fallbacks: tuple[MediaModelManifest, ...] = ()

    def resolved_enabled_modules(self) -> list[AgentModuleId]:
        out: list[AgentModuleId] = []
        for raw in self.enabled_module_ids:
            mid = AgentModuleId(str(raw).strip())
            out.append(mid)
        return out

    def resolved_per_module_browser(self) -> dict[AgentModuleId, bool]:
        return {AgentModuleId(k): v for k, v in self.per_module_browser.items()}

    def to_save_manifest_mapping(self) -> dict[str, object]:
        """Shape accepted by ``bundle_manifest_from_mapping`` / agent ``POST /manifest``."""

        return {
            "user_id": str(self.user_id),
            "litellm_base_url": self.litellm_base_url,
            "litellm_api_key": self.litellm_api_key,
            "template_variables": dict(self.template_variables),
            "enabled_modules": list(self.enabled_module_ids),
            "connected_integrations": sorted(k.value for k in self.connected_integrations),
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
            },
            "primary_channel": self.primary_channel,
            "proxy_url": self.proxy_url,
            "model_name_prefix": self.model_name_prefix,
            "agent_api_base_path": self.agent_api_base_path,
            "image_model": _media_model_to_mapping(self.image_model),
            "video_model": _media_model_to_mapping(self.video_model),
            "image_fallbacks": [_media_model_to_mapping(m) for m in self.image_fallbacks],
            "video_fallbacks": [_media_model_to_mapping(m) for m in self.video_fallbacks],
        }

    @staticmethod
    def from_yaml_file(path: Path, *, expand_env: bool = True) -> BundleManifest:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("YAML root must be a mapping")
        expanded = _expand_env_recursive(raw) if expand_env else raw
        if not isinstance(expanded, dict):
            raise ValueError("YAML root must be a mapping after env expansion")
        data: dict[str, object] = {str(k): v for k, v in expanded.items()}
        return bundle_manifest_from_mapping(data)


def _tuple_str(v: object) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,) if v.strip() else ()
    if isinstance(v, (list, tuple)):
        return tuple(str(x).strip() for x in v if str(x).strip())
    raise TypeError(f"Expected list or str, got {type(v)}")


def bundle_manifest_from_mapping(data: dict[str, object]) -> BundleManifest:
    """Build BundleManifest from a plain dict (e.g. after yaml.safe_load)."""
    tg_raw = data.get("telegram") or {}
    if not isinstance(tg_raw, dict):
        raise ValueError("telegram must be a mapping")
    telegram = TelegramManifest(
        enabled=bool(tg_raw.get("enabled", False)),
        bot_token=str(tg_raw.get("bot_token", "")),
        allowed_user_ids=_tuple_str(tg_raw.get("allowed_user_ids")),
        allowed_group_ids=_tuple_str(tg_raw.get("allowed_group_ids")),
    )

    ws_raw = data.get("web_search") or {}
    if not isinstance(ws_raw, dict):
        raise ValueError("web_search must be a mapping")
    web_search = WebSearchManifest(
        enabled=bool(ws_raw.get("enabled", False)),
    )

    enabled = data.get("enabled_modules") or []
    if not isinstance(enabled, (list, tuple)):
        raise TypeError("enabled_modules must be a list")
    enabled_ids = tuple(str(x).strip() for x in enabled if str(x).strip())

    conn = data.get("connected_integrations") or []
    if not isinstance(conn, (list, tuple)):
        raise TypeError("connected_integrations must be a list")
    connected = frozenset(IntegrationKind(str(x).strip()) for x in conn if str(x).strip())

    pmb = data.get("per_module_browser") or {}
    if not isinstance(pmb, dict):
        raise TypeError("per_module_browser must be a mapping")
    per_module_browser = {str(k): bool(v) for k, v in pmb.items()}

    tv = data.get("template_variables") or {}
    if not isinstance(tv, dict):
        raise TypeError("template_variables must be a mapping")
    template_variables = {str(k): str(v) for k, v in tv.items()}

    image_model = _media_model_from_mapping(data.get("image_model"), kind="image_model")
    video_model = _media_model_from_mapping(data.get("video_model"), kind="video_model")
    image_fallbacks = _media_fallbacks_from_mapping(data.get("image_fallbacks"), kind="image_fallbacks")
    video_fallbacks = _media_fallbacks_from_mapping(data.get("video_fallbacks"), kind="video_fallbacks")

    return BundleManifest(
        user_id=UUID(str(data["user_id"])),
        litellm_base_url=str(data["litellm_base_url"]),
        litellm_api_key=str(data["litellm_api_key"]),
        template_variables=template_variables,
        enabled_module_ids=enabled_ids,
        connected_integrations=connected,
        global_browser_enabled=bool(data.get("global_browser_enabled", True)),
        per_module_browser=per_module_browser,
        telegram=telegram,
        web_search=web_search,
        primary_channel=str(data.get("primary_channel", "sellerclaw-ui")),
        proxy_url=str(data.get("proxy_url") or "").strip(),
        model_name_prefix=str(data.get("model_name_prefix") or "").strip(),
        agent_api_base_path=_normalize_agent_api_base_path(data.get("agent_api_base_path")),
        image_model=image_model,
        video_model=video_model,
        image_fallbacks=image_fallbacks,
        video_fallbacks=video_fallbacks,
    )


def _media_model_from_mapping(raw: object, *, kind: str) -> MediaModelManifest:
    """Parse an optional ``{image,video}_model`` block; missing/empty → unconfigured default."""
    if raw is None:
        return MediaModelManifest()
    if not isinstance(raw, dict):
        raise TypeError(f"{kind} must be a mapping")
    return MediaModelManifest(
        model_id=str(raw.get("model_id") or "").strip(),
        display_name=str(raw.get("display_name") or "").strip(),
        openclaw_alias=str(raw.get("openclaw_alias") or "").strip(),
        litellm_route=str(raw.get("litellm_route") or "").strip(),
    )


def _media_model_to_mapping(m: MediaModelManifest) -> dict[str, str]:
    return {
        "model_id": m.model_id,
        "display_name": m.display_name,
        "openclaw_alias": m.openclaw_alias,
        "litellm_route": m.litellm_route,
    }


def _media_fallbacks_from_mapping(raw: object, *, kind: str) -> tuple[MediaModelManifest, ...]:
    """Parse an optional ``{image,video}_fallbacks`` list; unconfigured entries are skipped."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise TypeError(f"{kind} must be a list")
    parsed: list[MediaModelManifest] = []
    for entry in raw:
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise TypeError(f"{kind} entries must be mappings")
        model = MediaModelManifest(
            model_id=str(entry.get("model_id") or "").strip(),
            display_name=str(entry.get("display_name") or "").strip(),
            openclaw_alias=str(entry.get("openclaw_alias") or "").strip(),
            litellm_route=str(entry.get("litellm_route") or "").strip(),
        )
        if model.is_configured:
            parsed.append(model)
    return tuple(parsed)


def _normalize_agent_api_base_path(value: object) -> str:
    """Accept str/None, strip whitespace, require leading slash when non-empty."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError("agent_api_base_path must be a string")
    normalized = value.strip().rstrip("/")
    if not normalized:
        return ""
    if not normalized.startswith("/"):
        raise ValueError(
            f"agent_api_base_path must start with '/' when non-empty, got {normalized!r}"
        )
    return normalized
