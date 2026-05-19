from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.bundle.config_generator import (
    OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS,
    OPENCLAW_BUNDLE_CONSOLE_STYLE,
    OPENCLAW_BUNDLE_LOG_LEVEL,
    OPENCLAW_BUNDLE_REDACT_SENSITIVE,
    OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID,
    OPENCLAW_LOCAL_AGENT_BASE_URL,
    OPENCLAW_PLUGIN_PATH_SELLERCLAW_UI,
    OPENCLAW_PLUGIN_PATH_SELLERCLAW_WEB_SEARCH,
    SELLERCLAW_WEB_SEARCH_PLUGIN_ID,
    generate_openclaw_config,
)
from sellerclaw_agent.models import ModelTier

pytestmark = pytest.mark.unit

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_AGENT_API_KEY = "test-sca-agent-key"

_CANONICAL_COMPLEX = {
    "name": "Frontier (auto)",
    "reasoning": False,
    "input": ["text", "image"],
    "contextWindow": 128000,
    "maxTokens": 8192,
}
_CANONICAL_SIMPLE = {
    "name": "Mid (auto)",
    "reasoning": False,
    "input": ["text", "image"],
    "contextWindow": 128000,
    "maxTokens": 8192,
}
_CANONICAL_MINI = {
    "name": "Mini (auto)",
    "reasoning": False,
    "input": ["text", "image"],
    "contextWindow": 128000,
    "maxTokens": 8192,
}
_CANONICAL_IMAGE = {
    "name": "Image (auto)",
    "reasoning": False,
    "input": ["text", "image"],
    "contextWindow": 128000,
    "maxTokens": 8192,
}
_CANONICAL_VIDEO = {
    "name": "Video (auto)",
    "reasoning": False,
    "input": ["text", "image"],
    "contextWindow": 128000,
    "maxTokens": 8192,
}


def _supervisor_only(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> list[AssembledAgentConfig]:
    return [
        make_assembled_agent(
            tools_allow=["browser"],
            agents_md="# hi",
            memory_md="# m",
            soul_md=None,
            user_md=None,
            skills={},
        )
    ]


def test_generate_openclaw_config_has_gateway_and_models(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api/",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    # OpenClaw refuses to load configs without `meta.lastTouched*`: it flags them
    # as "missing-meta-vs-last-good" and silently restores the previous backup.
    assert isinstance(payload["meta"]["lastTouchedAt"], str)
    assert payload["meta"]["lastTouchedAt"].endswith("Z")
    assert payload["gateway"]["auth"]["token"] == "g"
    assert "litellm" in payload["models"]["providers"]
    assert payload["agents"]["list"][0]["id"] == "supervisor"
    assert payload["logging"]["level"] == OPENCLAW_BUNDLE_LOG_LEVEL
    assert payload["logging"]["consoleLevel"] == OPENCLAW_BUNDLE_LOG_LEVEL
    assert payload["logging"]["consoleStyle"] == OPENCLAW_BUNDLE_CONSOLE_STYLE
    assert payload["logging"]["redactSensitive"] == OPENCLAW_BUNDLE_REDACT_SENSITIVE
    assert payload["channels"]["sellerclaw-ui"]["apiBaseUrl"] == "http://api"
    assert (
        payload["plugins"]["entries"]["sellerclaw-ui"]["config"]["apiBaseUrl"] == "http://api"
    )
    assert (
        payload["channels"]["sellerclaw-ui"]["localAgentBaseUrl"]
        == OPENCLAW_LOCAL_AGENT_BASE_URL
    )
    assert (
        payload["plugins"]["entries"]["sellerclaw-ui"]["config"]["localAgentBaseUrl"]
        == OPENCLAW_LOCAL_AGENT_BASE_URL
    )
    assert (
        payload["plugins"]["entries"]["sellerclaw-ui"]["config"]["agentApiKey"] == _AGENT_API_KEY
    )
    assert (
        payload["plugins"]["entries"]["sellerclaw-ui"]["config"]["internalWebhookSecret"] == "h"
    )


def test_generate_openclaw_config_litellm_models_use_canonical_metadata(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_name_prefix="u:abc/",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    by_id = {
        m["id"]: m for m in json.loads(raw)["models"]["providers"]["litellm"]["models"]
    }
    assert by_id["u:abc/complex"] == {"id": "u:abc/complex", **_CANONICAL_COMPLEX}
    assert by_id["u:abc/simple"] == {"id": "u:abc/simple", **_CANONICAL_SIMPLE}
    assert by_id["u:abc/mini"] == {"id": "u:abc/mini", **_CANONICAL_MINI}
    assert by_id["u:abc/image"] == {"id": "u:abc/image", **_CANONICAL_IMAGE}
    assert by_id["u:abc/video"] == {"id": "u:abc/video", **_CANONICAL_VIDEO}


def test_generate_openclaw_config_telegram_channel_and_bindings(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=True,
        telegram_bot_token="bot-secret",
        telegram_allowed_user_ids=("123",),
        telegram_allowed_group_ids=("g1", "g2"),
    )
    payload = json.loads(raw)
    assert payload["channels"]["telegram"]["enabled"] is True
    assert payload["channels"]["telegram"]["botToken"] == "bot-secret"
    assert "tg:123" in payload["channels"]["telegram"]["allowFrom"]
    assert "g1" in payload["channels"]["telegram"]["groups"]
    assert "g2" in payload["channels"]["telegram"]["groups"]
    bindings = payload["bindings"]
    assert any(b.get("match") == {"channel": "telegram"} for b in bindings)


def test_generate_openclaw_config_web_search_enabled_wires_sellerclaw_plugin(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api/",
        sellerclaw_agent_api_base_url="http://api/agent",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
        web_search_enabled=True,
        web_search_auth_token="sca_test_token",
    )
    payload = json.loads(raw)
    assert SELLERCLAW_WEB_SEARCH_PLUGIN_ID in payload["plugins"]["allow"]
    entry = payload["plugins"]["entries"][SELLERCLAW_WEB_SEARCH_PLUGIN_ID]
    # The plugin appends ``/research/web-search`` to baseUrl — the derived
    # SELLERCLAW_AGENT_API_BASE_URL already includes the ``/agent`` prefix so
    # requests correctly resolve to ``POST /agent/research/web-search``.
    assert entry["config"]["webSearch"]["baseUrl"] == "http://api/agent"
    assert entry["config"]["webSearch"]["authToken"] == "sca_test_token"
    assert payload["tools"]["web"]["search"]["enabled"] is True
    assert payload["tools"]["web"]["search"]["provider"] == SELLERCLAW_WEB_SEARCH_PLUGIN_ID
    assert payload["plugins"]["load"]["paths"] == [
        OPENCLAW_PLUGIN_PATH_SELLERCLAW_UI,
        OPENCLAW_PLUGIN_PATH_SELLERCLAW_WEB_SEARCH,
    ]


def test_generate_openclaw_config_web_search_baseurl_falls_back_to_sellerclaw_api_url(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """When no derived URL is passed, the plugin baseUrl defaults to the bare host."""
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api/",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
        web_search_enabled=True,
        web_search_auth_token="sca_test_token",
    )
    payload = json.loads(raw)
    entry = payload["plugins"]["entries"][SELLERCLAW_WEB_SEARCH_PLUGIN_ID]
    assert entry["config"]["webSearch"]["baseUrl"] == "http://api"


def test_generate_openclaw_config_web_search_disabled_has_no_plugin_or_provider(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    assert SELLERCLAW_WEB_SEARCH_PLUGIN_ID not in payload["plugins"]["allow"]
    assert SELLERCLAW_WEB_SEARCH_PLUGIN_ID not in payload["plugins"]["entries"]
    assert payload["tools"]["web"]["search"] == {"enabled": False}
    assert payload["plugins"]["load"]["paths"] == [OPENCLAW_PLUGIN_PATH_SELLERCLAW_UI]


def test_generate_openclaw_config_browser_disabled(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
        browser_enabled=False,
    )
    assert json.loads(raw)["browser"]["enabled"] is False


def test_generate_openclaw_config_allowed_origins_in_control_ui(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
        allowed_origins=(
            "https://app.example.com",
            "https://admin.example.com/",
            "https://app.example.com",
        ),
    )
    payload = json.loads(raw)
    origins = payload["gateway"]["controlUi"]["allowedOrigins"]
    assert origins == ["https://app.example.com", "https://admin.example.com"]
    assert payload["gateway"]["controlUi"]["dangerouslyAllowHostHeaderOriginFallback"] is False


def test_generate_openclaw_config_model_name_prefix_on_litellm_groups(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_name_prefix="u:abc/",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    models = payload["models"]["providers"]["litellm"]["models"]
    ids = {m["id"] for m in models}
    assert ids == {
        "u:abc/complex",
        "u:abc/simple",
        "u:abc/mini",
        "u:abc/image",
        "u:abc/video",
    }
    assert payload["agents"]["list"][0]["model"] == "litellm/u:abc/complex"


@pytest.mark.parametrize(
    ("tier", "expected_suffix"),
    [
        pytest.param(ModelTier.COMPLEX, "complex", id="complex-tier"),
        pytest.param(ModelTier.SIMPLE, "simple", id="simple-tier"),
    ],
)
def test_generate_openclaw_config_agent_model_maps_tier(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    tier: ModelTier,
    expected_suffix: str,
) -> None:
    assembled = [
        make_assembled_agent(
            agent_id="worker",
            name="Worker",
            model_tier=tier,
            is_entry_point=False,
            subagent_ids=[],
            tools_allow=[],
            tools_deny=[],
            agents_md="# w",
            memory_md="# mw",
            soul_md=None,
            user_md=None,
            skills={},
        ),
        make_assembled_agent(
            subagent_ids=["worker"],
            tools_allow=["browser"],
            agents_md="# hi",
            memory_md="# m",
            soul_md=None,
            user_md=None,
            skills={},
        ),
    ]
    raw = generate_openclaw_config(
        assembled_agents=assembled,
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    by_id = {a["id"]: a for a in payload["agents"]["list"]}
    assert by_id["worker"]["model"] == f"litellm/{expected_suffix}"


@pytest.mark.parametrize(
    ("prefix", "expected_simple_ref", "expected_mini_ref"),
    [
        pytest.param(None, "litellm/simple", "litellm/mini", id="no-prefix"),
        pytest.param("u:abc/", "litellm/u:abc/simple", "litellm/u:abc/mini", id="user-prefix"),
    ],
)
def test_generate_openclaw_config_system_runs_route_per_tier(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    prefix: str | None,
    expected_simple_ref: str,
    expected_mini_ref: str,
) -> None:
    """Heartbeat uses the cheapest mini group; compaction / memory-flush stay on simple."""
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_name_prefix=prefix,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    defaults = payload["agents"]["defaults"]
    assert "heartbeat" not in defaults, "heartbeat must be per-agent (entry point only), not a default"
    assert defaults["compaction"]["model"] == expected_simple_ref
    assert defaults["compaction"]["memoryFlush"]["model"] == expected_simple_ref

    entry_point_payload = next(
        agent for agent in payload["agents"]["list"] if agent.get("default") is True
    )
    assert entry_point_payload["heartbeat"]["model"] == expected_mini_ref


def test_generate_openclaw_config_thinking_defaults_and_overrides(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Global default is `adaptive`; pure-executor subagents (shopify/ebay/supplier) opt out to `off`."""
    assembled = [
        make_assembled_agent(
            agent_id=agent_id,
            name=agent_id.capitalize(),
            model_tier=ModelTier.SIMPLE,
            is_entry_point=False,
            subagent_ids=[],
            tools_allow=[],
            tools_deny=[],
            agents_md=f"# {agent_id}",
            memory_md=f"# m-{agent_id}",
            soul_md=None,
            user_md=None,
            skills={},
        )
        for agent_id in ("shopify", "ebay", "supplier", "marketing", "scout")
    ] + [
        make_assembled_agent(
            subagent_ids=["shopify", "ebay", "supplier", "marketing", "scout"],
            tools_allow=["browser"],
            agents_md="# hi",
            memory_md="# m",
            soul_md=None,
            user_md=None,
            skills={},
        ),
    ]
    raw = generate_openclaw_config(
        assembled_agents=assembled,
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    assert payload["agents"]["defaults"]["thinkingDefault"] == "adaptive"
    by_id = {a["id"]: a for a in payload["agents"]["list"]}
    for opted_out in ("shopify", "ebay", "supplier"):
        assert by_id[opted_out]["thinkingDefault"] == "off", opted_out
    for inherits_default in ("supervisor", "marketing", "scout"):
        assert "thinkingDefault" not in by_id[inherits_default], inherits_default


def test_generate_openclaw_config_bootstrap_max_chars_in_defaults(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    assert (
        json.loads(raw)["agents"]["defaults"]["bootstrapMaxChars"] == OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS
    )


def test_generate_openclaw_config_web_search_enabled_requires_auth_token(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    with pytest.raises(ValueError, match="auth token"):
        generate_openclaw_config(
            assembled_agents=_supervisor_only(make_assembled_agent),
            gateway_token="g",
            hooks_token="h",
            agent_api_key=_AGENT_API_KEY,
            user_id=_USER_ID,
            sellerclaw_api_url="http://api/",
            litellm_base_url="http://litellm",
            litellm_api_key="k",
            telegram_enabled=False,
            telegram_bot_token="",
            telegram_allowed_user_ids=(),
            telegram_allowed_group_ids=(),
            web_search_enabled=True,
            web_search_auth_token="",
        )


def test_generate_openclaw_config_web_search_enabled_requires_api_base_url(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    with pytest.raises(ValueError, match="SELLERCLAW_API_URL"):
        generate_openclaw_config(
            assembled_agents=_supervisor_only(make_assembled_agent),
            gateway_token="g",
            hooks_token="h",
            agent_api_key=_AGENT_API_KEY,
            user_id=_USER_ID,
            sellerclaw_api_url="   ",
            litellm_base_url="http://litellm",
            litellm_api_key="k",
            telegram_enabled=False,
            telegram_bot_token="",
            telegram_allowed_user_ids=(),
            telegram_allowed_group_ids=(),
            web_search_enabled=True,
            web_search_auth_token="tok",
        )


def _generate_default_config(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    *,
    litellm_base_url: str = "http://litellm",
) -> Any:
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url=litellm_base_url,
        litellm_api_key="k",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    return json.loads(raw)


@pytest.mark.parametrize(
    ("litellm_base_url", "expected_anthropic_base_url", "expected_google_base_url"),
    [
        pytest.param(
            "http://litellm",
            "http://litellm/anthropic",
            "http://litellm/gemini",
            id="no-trailing-slash",
        ),
        pytest.param(
            "http://litellm/",
            "http://litellm/anthropic",
            "http://litellm/gemini",
            id="trailing-slash-stripped",
        ),
        pytest.param(
            "https://host.example.com/litellm",
            "https://host.example.com/litellm/anthropic",
            "https://host.example.com/litellm/gemini",
            id="subpath-host",
        ),
    ],
)
def test_generate_openclaw_config_emits_native_pdf_providers_via_litellm_passthrough(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    litellm_base_url: str,
    expected_anthropic_base_url: str,
    expected_google_base_url: str,
) -> None:
    """Anthropic + Google providers must be wired through LiteLLM passthrough endpoints so the
    PDF tool can flip into native mode (raw PDF bytes upstream, no extraction overhead)."""
    payload = _generate_default_config(
        make_assembled_agent,
        litellm_base_url=litellm_base_url,
    )
    providers = payload["models"]["providers"]
    assert providers["anthropic"]["baseUrl"] == expected_anthropic_base_url
    assert providers["anthropic"]["apiKey"] == "k"
    assert providers["google"]["baseUrl"] == expected_google_base_url
    assert providers["google"]["apiKey"] == "k"


def test_generate_openclaw_config_native_pdf_providers_carry_correct_model_metadata(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """PDF model ids are upstream-API names (LiteLLM passes them verbatim) — not prefixed."""
    providers = _generate_default_config(make_assembled_agent)["models"]["providers"]
    anthropic_models = providers["anthropic"]["models"]
    google_models = providers["google"]["models"]
    assert anthropic_models == [
        {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
        }
    ]
    assert google_models == [
        {
            "id": "gemini-3.1-pro-preview",
            "name": "Gemini 3.1 Pro (Preview)",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 1000000,
            "maxTokens": 8192,
        },
        {
            "id": "veo-3.1-fast-generate-preview",
            "name": "Google Veo 3.1 Fast",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 32000,
            "maxTokens": 8192,
        },
    ]


@pytest.mark.parametrize(
    ("prefix", "expected_image_ref"),
    [
        pytest.param(None, "litellm/image", id="no-prefix"),
        pytest.param("u:abc/", "litellm/u:abc/image", id="user-prefix"),
    ],
)
def test_generate_openclaw_config_image_generation_default_points_at_litellm_group(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    prefix: str | None,
    expected_image_ref: str,
) -> None:
    """Image generation routes through OpenClaw's LiteLLM image-generation provider plugin
    (``extensions/litellm/image-generation-provider.ts`` upstream); fallback between concrete
    image models lives inside LiteLLM, so the OpenClaw block must NOT carry its own
    ``fallbacks`` list."""
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_name_prefix=prefix,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    defaults = json.loads(raw)["agents"]["defaults"]
    assert defaults["imageGenerationModel"] == {"primary": expected_image_ref}


def test_generate_openclaw_config_video_generation_routes_through_google_passthrough(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Video generation must point at OpenClaw's ``google`` provider plugin — the LiteLLM
    extension has no video-generation plugin upstream (``extensions/litellm`` ships only
    ``image-generation-provider.ts``), so a ``litellm/...`` ref resolves to nothing and
    surfaces "no providers" at runtime. The Google video plugin posts to
    ``{baseUrl}/v1beta/models/{modelId}:predictLongRunning`` against LiteLLM's
    ``/gemini/{endpoint}`` passthrough configured in ``models.providers.google.baseUrl``."""
    defaults = _generate_default_config(make_assembled_agent)["agents"]["defaults"]
    assert defaults["videoGenerationModel"] == {
        "primary": "google/veo-3.1-fast-generate-preview",
    }


def test_generate_openclaw_config_pdf_model_default_prefers_anthropic_with_gemini_fallback(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Anthropic Sonnet is primary because it's stable (not Preview tier) and has a
    cleaner structured-extraction track record on invoices/tables/multilingual docs.
    Gemini Pro Preview sits in the fallback — useful for 1M-context outliers and as
    a circuit-breaker if the Anthropic API is degraded."""
    defaults = _generate_default_config(make_assembled_agent)["agents"]["defaults"]
    assert defaults["pdfModel"] == {
        "primary": "anthropic/claude-sonnet-4-6",
        "fallbacks": ["google/gemini-3.1-pro-preview"],
    }


def test_generate_openclaw_config_enables_document_extract_plugin_for_pdf_fallback(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """OpenClaw's PDF tool fails with `enable the document-extract plugin` when the
    bundled extractor isn't enabled — must always be in `plugins.allow` AND `plugins.entries`."""
    payload = _generate_default_config(make_assembled_agent)
    plugins = payload["plugins"]
    assert OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID in plugins["allow"]
    assert plugins["entries"][OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID] == {"enabled": True}


def test_generate_openclaw_config_model_name_prefix_does_not_leak_into_pdf_providers(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Prefix is for LiteLLM virtual groups; native provider ids must stay bare to be valid upstream."""
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        agent_api_key=_AGENT_API_KEY,
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_name_prefix="u:abc/",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    anthropic_ids = {m["id"] for m in payload["models"]["providers"]["anthropic"]["models"]}
    google_ids = {m["id"] for m in payload["models"]["providers"]["google"]["models"]}
    assert anthropic_ids == {"claude-sonnet-4-6"}
    assert google_ids == {"gemini-3.1-pro-preview", "veo-3.1-fast-generate-preview"}
    assert payload["agents"]["defaults"]["pdfModel"]["primary"] == "anthropic/claude-sonnet-4-6"
