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
class ModelSpec:
    """LiteLLM logical model group metadata for OpenClaw config."""

    id: str
    name: str
    reasoning: bool
    input: tuple[str, ...]
    context_window: int
    max_tokens: int


@dataclass(frozen=True)
class TelegramManifest:
    enabled: bool = False
    bot_token: str = ""
    allowed_user_ids: tuple[str, ...] = ()
    allowed_group_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class WebSearchManifest:
    enabled: bool = False
    provider: str | None = None
    api_key: str = ""


@dataclass(frozen=True)
class BundleManifest:
    """Flat input for bundle generation (caller supplies all template data and secrets)."""

    user_id: UUID
    gateway_token: str
    hooks_token: str
    litellm_base_url: str
    litellm_api_key: str
    model_complex: ModelSpec
    model_simple: ModelSpec
    webhook_api_base_url: str
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
    extra_allowed_origins: tuple[str, ...] = ()

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

        def _spec(m: ModelSpec) -> dict[str, object]:
            return {
                "id": m.id,
                "name": m.name,
                "reasoning": m.reasoning,
                "input": list(m.input),
                "context_window": m.context_window,
                "max_tokens": m.max_tokens,
            }

        return {
            "user_id": str(self.user_id),
            "gateway_token": self.gateway_token,
            "hooks_token": self.hooks_token,
            "litellm_base_url": self.litellm_base_url,
            "litellm_api_key": self.litellm_api_key,
            "models": {
                "complex": _spec(self.model_complex),
                "simple": _spec(self.model_simple),
            },
            "webhook_api_base_url": self.webhook_api_base_url,
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
                "provider": self.web_search.provider,
                "api_key": self.web_search.api_key,
            },
            "primary_channel": self.primary_channel,
            "proxy_url": self.proxy_url,
            "model_name_prefix": self.model_name_prefix,
            "extra_allowed_origins": list(self.extra_allowed_origins),
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


def _model_spec_from_mapping(m: object, *, label: str) -> ModelSpec:
    if not isinstance(m, dict):
        raise ValueError(f"{label} must be a mapping")
    inp = m.get("input", ["text"])
    if isinstance(inp, str):
        input_tuple = (inp,)
    else:
        input_tuple = tuple(str(x) for x in inp)
    return ModelSpec(
        id=str(m["id"]),
        name=str(m["name"]),
        reasoning=bool(m.get("reasoning", False)),
        input=input_tuple,
        context_window=int(m["context_window"]),
        max_tokens=int(m["max_tokens"]),
    )


def bundle_manifest_from_mapping(data: dict[str, object]) -> BundleManifest:
    """Build BundleManifest from a plain dict (e.g. after yaml.safe_load)."""
    models = data["models"]
    if not isinstance(models, dict):
        raise ValueError("models must be a mapping")
    complex_m = models.get("complex")
    simple_m = models.get("simple")
    if not isinstance(complex_m, dict) or not isinstance(simple_m, dict):
        raise ValueError("models.complex and models.simple are required")
    model_complex = _model_spec_from_mapping(complex_m, label="models.complex")
    model_simple = _model_spec_from_mapping(simple_m, label="models.simple")

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
    prov = ws_raw.get("provider")
    web_search = WebSearchManifest(
        enabled=bool(ws_raw.get("enabled", False)),
        provider=str(prov) if prov not in (None, "") else None,
        api_key=str(ws_raw.get("api_key", "")),
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

    eao_raw = data.get("extra_allowed_origins") or []
    if not isinstance(eao_raw, (list, tuple)):
        raise TypeError("extra_allowed_origins must be a list")
    extra_allowed_origins = tuple(
        str(x).strip().rstrip("/") for x in eao_raw if str(x).strip()
    )

    return BundleManifest(
        user_id=UUID(str(data["user_id"])),
        gateway_token=str(data["gateway_token"]),
        hooks_token=str(data["hooks_token"]),
        litellm_base_url=str(data["litellm_base_url"]),
        litellm_api_key=str(data["litellm_api_key"]),
        model_complex=model_complex,
        model_simple=model_simple,
        webhook_api_base_url=str(data["webhook_api_base_url"]),
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
        extra_allowed_origins=extra_allowed_origins,
    )
