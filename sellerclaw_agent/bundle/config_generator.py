from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import UUID

from sellerclaw_agent.bundle.manifest import ModelSpec
from sellerclaw_agent.bundle.protocols import AssembledAgentLike
from sellerclaw_agent.models import ModelTier


def _agent_tier_value(agent: AssembledAgentLike) -> str:
    """Support both sellerclaw_agent and monolith ModelTier enums."""
    tier = agent.model_tier
    if isinstance(tier, ModelTier):
        return tier.value
    val = getattr(tier, "value", None)
    return val if isinstance(val, str) else str(tier)

_LITELLM_OPENCLAW_PROVIDER = "litellm"

# OpenClaw gateway logging in generated config (not user/manifest input).
OPENCLAW_BUNDLE_LOG_LEVEL = "warn"
OPENCLAW_BUNDLE_CONSOLE_STYLE = "pretty"
OPENCLAW_BUNDLE_REDACT_SENSITIVE = "tools"

OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS = 30000

# Local sellerclaw-agent HTTP port inside the runtime container; plugins call back to it
# via loopback for media upload proxying. Kept as a module constant so bundle tests can
# assert the emitted config.
OPENCLAW_LOCAL_AGENT_BASE_URL = "http://127.0.0.1:8001"

# Keyless web search: plugin id, OpenClaw web-search provider id, and directory under /opt/openclaw-plugins/.
SELLERCLAW_WEB_SEARCH_PLUGIN_ID = "sellerclaw-web-search"
OPENCLAW_PLUGIN_PATH_SELLERCLAW_UI = "/opt/openclaw-plugins/sellerclaw-ui"
OPENCLAW_PLUGIN_PATH_SELLERCLAW_WEB_SEARCH = "/opt/openclaw-plugins/sellerclaw-web-search"


def _openclaw_litellm_model_ref(group_model_name: str) -> str:
    return f"{_LITELLM_OPENCLAW_PROVIDER}/{group_model_name}"


def _build_group_model_entry(*, model_name: str, source: ModelSpec) -> dict[str, object]:
    return {
        "id": model_name,
        "name": source.name,
        "reasoning": source.reasoning,
        "input": list(source.input),
        "contextWindow": source.context_window,
        "maxTokens": source.max_tokens,
    }


def _build_telegram_groups(*, group_ids: list[str]) -> dict[str, dict[str, bool]]:
    result: dict[str, dict[str, bool]] = {}
    for gid in group_ids:
        normalized = gid.strip()
        if normalized:
            result[normalized] = {"requireMention": True}
    return result


def _build_control_ui_config(
    *,
    allowed_origins: tuple[str, ...],
) -> dict[str, object]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in allowed_origins:
        normalized = item.strip().rstrip("/")
        if not normalized or normalized in seen:
            continue
        unique.append(normalized)
        seen.add(normalized)
    return {
        "allowedOrigins": unique,
        "dangerouslyAllowHostHeaderOriginFallback": False,
    }


def _merge_openclaw_channels(
    *,
    telegram_channel: dict[str, object] | None,
    sellerclaw_ui: dict[str, object],
) -> dict[str, object]:
    channels: dict[str, object] = {"sellerclaw-ui": sellerclaw_ui}
    if telegram_channel is not None:
        channels["telegram"] = telegram_channel
    return channels


def generate_openclaw_config(
    *,
    assembled_agents: Sequence[AssembledAgentLike],
    gateway_token: str,
    hooks_token: str,
    user_id: UUID,
    sellerclaw_api_url: str,
    sellerclaw_agent_api_base_url: str | None = None,
    litellm_base_url: str,
    litellm_api_key: str,
    model_complex: ModelSpec,
    model_simple: ModelSpec,
    model_name_prefix: str | None = None,
    telegram_enabled: bool,
    telegram_bot_token: str,
    telegram_allowed_user_ids: tuple[str, ...],
    telegram_allowed_group_ids: tuple[str, ...],
    allowed_origins: tuple[str, ...] = (),
    browser_enabled: bool = True,
    web_search_enabled: bool = False,
    web_search_auth_token: str = "",
    primary_channel: str = "sellerclaw-ui",
) -> str:
    """Build OpenClaw JSON config from assembled agents and flat parameters."""
    complex_group = f"{model_name_prefix}complex" if model_name_prefix else "complex"
    simple_group = f"{model_name_prefix}simple" if model_name_prefix else "simple"
    litellm_models = [
        _build_group_model_entry(model_name=complex_group, source=model_complex),
        _build_group_model_entry(model_name=simple_group, source=model_simple),
    ]

    agent_ids = [agent.agent_id for agent in assembled_agents]
    entry_point = next(agent.agent_id for agent in assembled_agents if agent.is_entry_point)

    telegram_token = (telegram_bot_token or "").strip()
    telegram_on = telegram_enabled and bool(telegram_token)
    telegram_allow_from = [f"tg:{uid.strip()}" for uid in telegram_allowed_user_ids if uid.strip()]
    telegram_groups = _build_telegram_groups(
        group_ids=[gid.strip() for gid in telegram_allowed_group_ids if str(gid).strip()]
    )
    has_telegram_allowlist = bool(telegram_allow_from) or bool(telegram_groups)
    telegram_policy = "allowlist" if has_telegram_allowlist else "open"
    telegram_channel: dict[str, object] | None = None
    telegram_bindings: list[dict[str, object]] = []
    if telegram_on:
        telegram_channel = {
            "enabled": True,
            "botToken": telegram_token,
            "dmPolicy": telegram_policy,
            "allowFrom": telegram_allow_from,
            "groupPolicy": telegram_policy,
            "groups": telegram_groups,
        }
        telegram_bindings = [
            {
                "agentId": entry_point,
                "match": {"channel": "telegram"},
            },
        ]

    # Derived agent API base URL (SELLERCLAW_AGENT_API_BASE_URL). Defaults to the
    # bare SELLERCLAW_API_URL when the caller doesn't supply a derived value, which
    # keeps older call sites (and tests) working without an explicit path segment.
    effective_agent_api_base_url = (
        sellerclaw_agent_api_base_url
        if sellerclaw_agent_api_base_url is not None
        else sellerclaw_api_url
    )
    effective_agent_api_base_url = (effective_agent_api_base_url or "").strip().rstrip("/")

    if web_search_enabled:
        if not (web_search_auth_token or "").strip():
            raise ValueError(
                "Web search auth token is required when web search is enabled "
                "(agent bearer from agent_token.json or AGENT_API_KEY)."
            )
        if not effective_agent_api_base_url:
            raise ValueError(
                "SellerClaw API base URL is required when web search is enabled (SELLERCLAW_API_URL)."
            )

    plugin_load_paths: list[str] = [OPENCLAW_PLUGIN_PATH_SELLERCLAW_UI]
    if web_search_enabled:
        plugin_load_paths.append(OPENCLAW_PLUGIN_PATH_SELLERCLAW_WEB_SEARCH)

    web_search_plugin_entry: dict[str, object] | None = None
    web_search_plugin_id: str | None = None
    if web_search_enabled:
        web_search_plugin_id = SELLERCLAW_WEB_SEARCH_PLUGIN_ID
        web_search_plugin_entry = {
            "enabled": True,
            "config": {
                "webSearch": {
                    "baseUrl": effective_agent_api_base_url,
                    "authToken": (web_search_auth_token or "").strip(),
                }
            },
        }

    if web_search_enabled:
        web_search_tools: dict[str, object] = {
            "enabled": True,
            "provider": SELLERCLAW_WEB_SEARCH_PLUGIN_ID,
        }
    else:
        web_search_tools = {"enabled": False}

    agents_list: list[dict[str, object]] = []
    default_primary = _openclaw_litellm_model_ref(complex_group)
    for agent in assembled_agents:
        group = complex_group if _agent_tier_value(agent) == ModelTier.COMPLEX.value else simple_group
        agent_model = _openclaw_litellm_model_ref(group)
        payload: dict[str, object] = {
            "id": agent.agent_id,
            "name": agent.name,
            "workspace": f"/home/node/.openclaw/workspace-{agent.agent_id}",
            "model": agent_model,
            "subagents": {"allowAgents": list(agent.subagent_ids)},
            "tools": {"allow": list(agent.tools_allow), "deny": list(agent.tools_deny)},
        }
        if agent.is_entry_point:
            payload["default"] = True
        agents_list.append(payload)

    sellerclaw_ui_plugin_config: dict[str, object] = {
        "apiBaseUrl": sellerclaw_api_url.strip().rstrip("/"),
        "userId": str(user_id),
        "internalWebhookSecret": hooks_token,
        "primaryChannel": primary_channel,
        "localAgentBaseUrl": OPENCLAW_LOCAL_AGENT_BASE_URL,
    }

    config_payload = {
        "logging": {
            "level": OPENCLAW_BUNDLE_LOG_LEVEL,
            "consoleLevel": OPENCLAW_BUNDLE_LOG_LEVEL,
            "consoleStyle": OPENCLAW_BUNDLE_CONSOLE_STYLE,
            "redactSensitive": OPENCLAW_BUNDLE_REDACT_SENSITIVE,
        },
        "gateway": {
            "mode": "local",
            "auth": {"mode": "token", "token": gateway_token},
            "trustedProxies": ["127.0.0.0/8", "172.16.0.0/12"],
            "controlUi": _build_control_ui_config(
                allowed_origins=allowed_origins,
            ),
            "http": {
                "endpoints": {
                    "responses": {"enabled": True},
                },
            },
        },
        "hooks": {
            "enabled": True,
            "token": hooks_token,
            "path": "/hooks",
            "defaultSessionKey": "hook:dev",
            "allowRequestSessionKey": True,
            "allowedSessionKeyPrefixes": ["hook:", "agent:"],
            "allowedAgentIds": agent_ids,
        },
        "models": {
            "providers": {
                "litellm": {
                    "baseUrl": litellm_base_url,
                    "apiKey": litellm_api_key,
                    "api": "openai-completions",
                    "models": litellm_models,
                }
            }
        },
        "agents": {
            "defaults": {
                "skipBootstrap": True,
                "timeoutSeconds": 600,
                "bootstrapMaxChars": OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS,
                "model": {"primary": default_primary},
                "thinkingDefault": "off",
                "blockStreamingDefault": "on",
                "blockStreamingChunk": {
                    "minChars": 100,
                    "maxChars": 300,
                    "breakPreference": "sentence",
                },
                "compaction": {
                    "reserveTokensFloor": 20000,
                    "memoryFlush": {"enabled": True, "softThresholdTokens": 10000},
                },
                "subagents": {
                    "runTimeoutSeconds": 600,
                },
            },
            "list": agents_list,
        },
        "bindings": [
            *telegram_bindings,
            {
                "agentId": entry_point,
                "match": {"channel": "sellerclaw-ui"},
            },
        ],
        "session": {
            "dmScope": "per-channel-peer",
            "reset": {"mode": "idle"},
            "agentToAgent": {"maxPingPongTurns": 5},
        },
        "channels": _merge_openclaw_channels(
            telegram_channel=telegram_channel,
            sellerclaw_ui=sellerclaw_ui_plugin_config,
        ),
        "plugins": {
            "enabled": True,
            "allow": [
                "sellerclaw-ui",
                "browser",
                *(
                    [web_search_plugin_id]
                    if web_search_enabled and web_search_plugin_id is not None
                    else []
                ),
            ],
            "load": {"paths": plugin_load_paths},
            "entries": {
                "sellerclaw-ui": {"enabled": True, "config": sellerclaw_ui_plugin_config},
                **(
                    {web_search_plugin_id: web_search_plugin_entry}
                    if web_search_enabled
                    and web_search_plugin_id is not None
                    and web_search_plugin_entry is not None
                    else {}
                ),
            },
        },
        "browser": {
            "enabled": browser_enabled,
            "defaultProfile": "openclaw",
            "headless": False,
            "noSandbox": True,
            "executablePath": "/usr/local/bin/openclaw_chrome",
            "remoteCdpTimeoutMs": 10000,
            "remoteCdpHandshakeTimeoutMs": 30000,
            "profiles": {},
        },
        "cron": {"enabled": True},
        "tools": {
            "web": {
                "fetch": {"enabled": True},
                "search": web_search_tools,
            },
            "exec": {"security": "full", "ask": "off"},
            "sessions": {"visibility": "all"},
            "agentToAgent": {"enabled": True},
        },
    }
    return json.dumps(config_payload, indent=2)
