from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

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

# Canonical OpenClaw metadata for LiteLLM virtual model groups
# `{prefix}complex` / `{prefix}simple` / `{prefix}mini` / `{prefix}image` / `{prefix}video`.
# Routing to concrete provider models is configured on the LiteLLM side — these entries
# only register the group name + display metadata that OpenClaw exposes to agents/UI.
_OPENCLAW_LITELLM_COMPLEX_DISPLAY_NAME = "Frontier (auto)"
_OPENCLAW_LITELLM_SIMPLE_DISPLAY_NAME = "Mid (auto)"
_OPENCLAW_LITELLM_MINI_DISPLAY_NAME = "Mini (auto)"
_OPENCLAW_LITELLM_IMAGE_DISPLAY_NAME = "Image (auto)"
_OPENCLAW_LITELLM_VIDEO_DISPLAY_NAME = "Video (auto)"
_OPENCLAW_LITELLM_GROUP_REASONING = False
_OPENCLAW_LITELLM_GROUP_INPUT: tuple[str, ...] = ("text", "image")
_OPENCLAW_LITELLM_CONTEXT_WINDOW = 128000
_OPENCLAW_LITELLM_MAX_TOKENS = 8192

# Native-PDF providers, exposed via LiteLLM's passthrough endpoints
# (`<litellm>/anthropic/v1/messages`, `<litellm>/gemini/v1beta/...`). OpenClaw
# routes through its real `anthropic`/`google` provider drivers — which is what
# activates native PDF mode (raw PDF bytes to provider API, `pages` filter, no
# extraction overhead). Model ids must be the upstream provider's real names
# because LiteLLM passes them through verbatim; do NOT prefix with model_name_prefix.
_OPENCLAW_PDF_INPUT: tuple[str, ...] = ("text", "image")

_ANTHROPIC_PASSTHROUGH_SUBPATH = "/anthropic"
# OpenClaw's Google provider IGNORES the path component of ``models.providers.google.baseUrl``
# and always builds the request URL with a hardcoded ``/gemini/models/{model}:{action}``
# template appended to the host (empirically verified — not in docs). So the path we
# put here doesn't reach OpenClaw's URL builder; it always becomes ``/gemini/...``.
# The sellerclaw-api reverse proxy then rewrites ``/litellm/gemini/...`` →
# ``/litellm/gemini-passthrough/...`` so the request lands on our custom LiteLLM
# ``pass_through_endpoints`` entry whose ``target`` already embeds ``/v1beta``.
# (See ``src/litellm_proxy/infra/views.py`` ``_GEMINI_PASSTHROUGH_*`` constants and
# ``deploy/common/litellm/config.template.yaml`` ``/gemini-passthrough`` entry.)
# Value here exists for ``baseUrl`` construction only — pick anything that ends in
# a literal Google-ish path so the configured URL stays self-documenting.
_GOOGLE_PASSTHROUGH_SUBPATH = "/gemini"

_PDF_ANTHROPIC_MODEL_ID = "claude-sonnet-4-6"
_PDF_ANTHROPIC_MODEL_NAME = "Claude Sonnet 4.6"
_PDF_ANTHROPIC_CONTEXT_WINDOW = 200000
_PDF_ANTHROPIC_MAX_TOKENS = 8192

_PDF_GOOGLE_MODEL_ID = "gemini-3.1-pro-preview"
_PDF_GOOGLE_MODEL_NAME = "Gemini 3.1 Pro (Preview)"
_PDF_GOOGLE_CONTEXT_WINDOW = 1000000
_PDF_GOOGLE_MAX_TOKENS = 8192

# Veo (video) routes through the same `models.providers.google` passthrough as PDF —
# OpenClaw's google video-generation provider plugin reads ``models.providers.google.baseUrl``
# and posts to ``{baseUrl}/v1beta/models/{modelId}:predictLongRunning``, which LiteLLM's
# ``/gemini/{endpoint}`` catchall forwards to Google verbatim. There is no LiteLLM
# video-generation plugin in OpenClaw's ``extensions/litellm`` (only image), so routing
# ``videoGenerationModel.primary`` through the LiteLLM virtual provider is a no-op — the
# tool surfaces "no providers" until a recognized provider plugin (google here) is
# discoverable. Model id must be the Google-recognized name; OpenClaw's defaults are
# enumerated in ``extensions/google/generation-provider-metadata.ts`` upstream.
_VIDEO_GOOGLE_MODEL_ID = "veo-3.1-fast-generate-preview"
_VIDEO_GOOGLE_MODEL_NAME = "Google Veo 3.1 Fast"
_VIDEO_GOOGLE_CONTEXT_WINDOW = 32000
_VIDEO_GOOGLE_MAX_TOKENS = 8192

# OpenClaw gateway logging in generated config (not user/manifest input).
OPENCLAW_BUNDLE_LOG_LEVEL = "warn"
OPENCLAW_BUNDLE_CONSOLE_STYLE = "pretty"
OPENCLAW_BUNDLE_REDACT_SENSITIVE = "tools"

OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS = 30000

# Pure-executor subagents whose work is API call → parse → return. Reasoning adds latency
# and cost without changing outcomes here, so they opt out of the global adaptive default.
_NO_THINKING_AGENT_IDS = frozenset({"shopify", "ebay", "supplier"})

# Local sellerclaw-agent HTTP port inside the runtime container; plugins call back to it
# via loopback for media upload proxying. Kept as a module constant so bundle tests can
# assert the emitted config.
OPENCLAW_LOCAL_AGENT_BASE_URL = "http://127.0.0.1:8001"

# Keyless web search: plugin id, OpenClaw web-search provider id, and directory under /opt/openclaw-plugins/.
SELLERCLAW_WEB_SEARCH_PLUGIN_ID = "sellerclaw-web-search"
OPENCLAW_PLUGIN_PATH_SELLERCLAW_UI = "/opt/openclaw-plugins/sellerclaw-ui"
OPENCLAW_PLUGIN_PATH_SELLERCLAW_WEB_SEARCH = "/opt/openclaw-plugins/sellerclaw-web-search"

# Bundled OpenClaw plugin that backs the PDF tool's fallback (extract + page-render)
# pipeline. Without it the PDF tool fails with `PDF extraction disabled or unavailable:
# enable the document-extract plugin to process application/pdf files`. Bundled with
# the OpenClaw runtime, so no load path needed — just allow + enable.
OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID = "document-extract"


def _openclaw_litellm_model_ref(group_model_name: str) -> str:
    return f"{_LITELLM_OPENCLAW_PROVIDER}/{group_model_name}"


def _build_litellm_group_entry(group_id: str, display_name: str) -> dict[str, object]:
    return {
        "id": group_id,
        "name": display_name,
        "reasoning": _OPENCLAW_LITELLM_GROUP_REASONING,
        "input": list(_OPENCLAW_LITELLM_GROUP_INPUT),
        "contextWindow": _OPENCLAW_LITELLM_CONTEXT_WINDOW,
        "maxTokens": _OPENCLAW_LITELLM_MAX_TOKENS,
    }


def _build_litellm_openclaw_model_groups(
    *,
    complex_group: str,
    simple_group: str,
    mini_group: str,
    image_group: str,
    video_group: str,
) -> list[dict[str, object]]:
    """Five virtual groups served through LiteLLM: chat tiers + media kinds.

    `image`/`video` follow the same operator-static, LiteLLM-side-fallback pattern as
    `complex`/`simple`/`mini` — the agent only sees a single ref per kind, fallback
    between concrete models is configured inside LiteLLM's model_list.
    """
    return [
        _build_litellm_group_entry(complex_group, _OPENCLAW_LITELLM_COMPLEX_DISPLAY_NAME),
        _build_litellm_group_entry(simple_group, _OPENCLAW_LITELLM_SIMPLE_DISPLAY_NAME),
        _build_litellm_group_entry(mini_group, _OPENCLAW_LITELLM_MINI_DISPLAY_NAME),
        _build_litellm_group_entry(image_group, _OPENCLAW_LITELLM_IMAGE_DISPLAY_NAME),
        _build_litellm_group_entry(video_group, _OPENCLAW_LITELLM_VIDEO_DISPLAY_NAME),
    ]


def _derive_passthrough_base_url(litellm_base_url: str, subpath: str) -> str:
    """LiteLLM exposes provider-native APIs under fixed subpaths of its base URL.

    The provider SDK in OpenClaw appends the rest of the path (`/v1/messages`,
    `/v1beta/models/...`) — we only carry the prefix here.
    """
    return f"{litellm_base_url.rstrip('/')}{subpath}"


def _build_anthropic_passthrough_provider(
    *,
    litellm_base_url: str,
    litellm_api_key: str,
) -> dict[str, object]:
    return {
        "baseUrl": _derive_passthrough_base_url(litellm_base_url, _ANTHROPIC_PASSTHROUGH_SUBPATH),
        "apiKey": litellm_api_key,
        "models": [
            {
                "id": _PDF_ANTHROPIC_MODEL_ID,
                "name": _PDF_ANTHROPIC_MODEL_NAME,
                "reasoning": False,
                "input": list(_OPENCLAW_PDF_INPUT),
                "contextWindow": _PDF_ANTHROPIC_CONTEXT_WINDOW,
                "maxTokens": _PDF_ANTHROPIC_MAX_TOKENS,
            },
        ],
    }


def _build_google_passthrough_provider(
    *,
    litellm_base_url: str,
    litellm_api_key: str,
) -> dict[str, object]:
    return {
        "baseUrl": _derive_passthrough_base_url(litellm_base_url, _GOOGLE_PASSTHROUGH_SUBPATH),
        "apiKey": litellm_api_key,
        "models": [
            {
                "id": _PDF_GOOGLE_MODEL_ID,
                "name": _PDF_GOOGLE_MODEL_NAME,
                "reasoning": False,
                "input": list(_OPENCLAW_PDF_INPUT),
                "contextWindow": _PDF_GOOGLE_CONTEXT_WINDOW,
                "maxTokens": _PDF_GOOGLE_MAX_TOKENS,
            },
            {
                "id": _VIDEO_GOOGLE_MODEL_ID,
                "name": _VIDEO_GOOGLE_MODEL_NAME,
                "reasoning": False,
                "input": list(_OPENCLAW_PDF_INPUT),
                "contextWindow": _VIDEO_GOOGLE_CONTEXT_WINDOW,
                "maxTokens": _VIDEO_GOOGLE_MAX_TOKENS,
            },
        ],
    }


def _build_pdf_model_block() -> dict[str, object]:
    """OpenClaw `agents.defaults.pdfModel`. Primary picks Anthropic Sonnet (stable,
    cleaner structured-extraction track record on invoices/tables/multilingual
    docs); falls back to Gemini Pro (Preview tier — useful for 1M-context outliers
    and as a circuit-breaker if Anthropic API is degraded)."""
    return {
        "primary": f"anthropic/{_PDF_ANTHROPIC_MODEL_ID}",
        "fallbacks": [f"google/{_PDF_GOOGLE_MODEL_ID}"],
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
    agent_api_key: str,
    user_id: UUID,
    sellerclaw_api_url: str,
    sellerclaw_agent_api_base_url: str | None = None,
    litellm_base_url: str,
    litellm_api_key: str,
    model_name_prefix: str | None = None,
    created_at: datetime | None = None,
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
    mini_group = f"{model_name_prefix}mini" if model_name_prefix else "mini"
    image_group = f"{model_name_prefix}image" if model_name_prefix else "image"
    video_group = f"{model_name_prefix}video" if model_name_prefix else "video"
    litellm_models = _build_litellm_openclaw_model_groups(
        complex_group=complex_group,
        simple_group=simple_group,
        mini_group=mini_group,
        image_group=image_group,
        video_group=video_group,
    )

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
    simple_primary = _openclaw_litellm_model_ref(simple_group)
    mini_primary = _openclaw_litellm_model_ref(mini_group)
    image_primary = _openclaw_litellm_model_ref(image_group)
    # Video routes through OpenClaw's `google` video-generation provider (which talks to
    # LiteLLM's `/gemini/{endpoint}` passthrough via the configured provider baseUrl).
    # OpenClaw has no `litellm` video plugin, so a `litellm/...` ref resolves to nothing
    # at runtime and the `video_generate` tool reports "no providers".
    video_primary = f"google/{_VIDEO_GOOGLE_MODEL_ID}"
    for agent in assembled_agents:
        group = complex_group if _agent_tier_value(agent) == ModelTier.COMPLEX.value else simple_group
        agent_model = _openclaw_litellm_model_ref(group)
        payload: dict[str, object] = {
            "id": agent.agent_id,
            "name": agent.name,
            "workspace": f"/home/node/.openclaw/workspace-{agent.agent_id}",
            "model": agent_model,
            "subagents": {
                "allowAgents": list(agent.subagent_ids),
                # Block `sessions_spawn` without an `agentId`. Without this, OpenClaw
                # falls back to spawning a clone of the requester — which for the
                # supervisor means a workspace without platform skills and the LLM
                # rediscovering CLI commands via --help until it times out.
                "requireAgentId": True,
            },
            "tools": {"allow": list(agent.tools_allow), "deny": list(agent.tools_deny)},
        }
        if agent.is_entry_point:
            payload["default"] = True
            payload["heartbeat"] = {"model": mini_primary}
        # Per-agent thinking override comes from the manifest; fall back to the static
        # pure-executor allowlist when the manifest doesn't carry an explicit value.
        thinking_default = getattr(agent, "thinking_default", None)
        if isinstance(thinking_default, str) and thinking_default.strip():
            payload["thinkingDefault"] = thinking_default.strip()
        elif agent.agent_id in _NO_THINKING_AGENT_IDS:
            payload["thinkingDefault"] = "off"
        agents_list.append(payload)

    sellerclaw_ui_plugin_config: dict[str, object] = {
        "apiBaseUrl": sellerclaw_api_url.strip().rstrip("/"),
        "userId": str(user_id),
        "agentApiKey": (agent_api_key or "").strip(),
        "internalWebhookSecret": hooks_token,
        "primaryChannel": primary_channel,
        "localAgentBaseUrl": OPENCLAW_LOCAL_AGENT_BASE_URL,
    }

    # OpenClaw treats `meta` as the marker that the file came from a trusted writer:
    # if `lastKnownGood` had `lastTouchedVersion`/`lastTouchedAt` but our new file
    # doesn't, OpenClaw flags `missing-meta-vs-last-good` and silently restores the
    # previous backup, dropping every fresh value (including the rotated agentApiKey).
    last_touched_at = (created_at or datetime.now(tz=UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    config_payload = {
        "meta": {"lastTouchedAt": last_touched_at},
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
                },
                # Native-PDF providers pointed at LiteLLM passthrough endpoints.
                # OpenClaw drives them through its real anthropic/google SDKs, which is
                # what flips the PDF tool into native mode (raw PDF bytes upstream, no
                # extraction/render). Upstream auth happens inside LiteLLM; OpenClaw only
                # presents the LiteLLM virtual key.
                "anthropic": _build_anthropic_passthrough_provider(
                    litellm_base_url=litellm_base_url,
                    litellm_api_key=litellm_api_key,
                ),
                "google": _build_google_passthrough_provider(
                    litellm_base_url=litellm_base_url,
                    litellm_api_key=litellm_api_key,
                ),
            }
        },
        "agents": {
            "defaults": {
                "skipBootstrap": True,
                "timeoutSeconds": 600,
                "bootstrapMaxChars": OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS,
                "model": {"primary": default_primary},
                # `imageGenerationModel` / `videoGenerationModel` always point at the LiteLLM
                # virtual group. Fallback between concrete provider models is handled inside
                # LiteLLM's model_list (operator-configured); OpenClaw sees a single ref per kind.
                "imageGenerationModel": {"primary": image_primary},
                "videoGenerationModel": {"primary": video_primary},
                "pdfModel": _build_pdf_model_block(),
                "thinkingDefault": "adaptive",
                "blockStreamingDefault": "on",
                "blockStreamingChunk": {
                    "minChars": 100,
                    "maxChars": 1500,
                    "breakPreference": "newline",
                },
                "compaction": {
                    "reserveTokensFloor": 20000,
                    "model": simple_primary,
                    "memoryFlush": {
                        "enabled": True,
                        "softThresholdTokens": 10000,
                        "model": simple_primary,
                    },
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
                OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID,
                *(
                    [web_search_plugin_id]
                    if web_search_enabled and web_search_plugin_id is not None
                    else []
                ),
            ],
            "load": {"paths": plugin_load_paths},
            "entries": {
                "sellerclaw-ui": {"enabled": True, "config": sellerclaw_ui_plugin_config},
                OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID: {"enabled": True},
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
