from __future__ import annotations

import json
from collections.abc import Callable
from uuid import UUID

import pytest
from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.bundle.config_generator import (
    OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS,
    OPENCLAW_BUNDLE_CONSOLE_STYLE,
    OPENCLAW_BUNDLE_LOG_LEVEL,
    OPENCLAW_BUNDLE_REDACT_SENSITIVE,
    OPENCLAW_LOCAL_AGENT_BASE_URL,
    generate_openclaw_config,
)
from sellerclaw_agent.bundle.manifest import ModelSpec
from sellerclaw_agent.models import ModelTier

pytestmark = pytest.mark.unit

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")


def _base_specs() -> tuple[ModelSpec, ModelSpec]:
    mc = ModelSpec(
        id="c1", name="C", reasoning=True, input=("text",), context_window=100, max_tokens=50
    )
    ms = ModelSpec(
        id="s1", name="S", reasoning=False, input=("text",), context_window=100, max_tokens=50
    )
    return mc, ms


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
    mc, ms = _base_specs()
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        user_id=_USER_ID,
        sellerclaw_api_url="http://api/",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_complex=mc,
        model_simple=ms,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
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


def test_generate_openclaw_config_telegram_channel_and_bindings(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    mc, ms = _base_specs()
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_complex=mc,
        model_simple=ms,
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


def test_generate_openclaw_config_web_search_plugin_and_tools(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    mc, ms = _base_specs()
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_complex=mc,
        model_simple=ms,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
        web_search_enabled=True,
        web_search_provider="brave",
        web_search_api_key="ws-key",
    )
    payload = json.loads(raw)
    assert "brave" in payload["plugins"]["allow"]
    assert payload["plugins"]["entries"]["brave"]["config"]["webSearch"]["apiKey"] == "ws-key"
    assert payload["tools"]["web"]["search"]["enabled"] is True


def test_generate_openclaw_config_browser_disabled(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    mc, ms = _base_specs()
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_complex=mc,
        model_simple=ms,
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
    mc, ms = _base_specs()
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_complex=mc,
        model_simple=ms,
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
    mc, ms = _base_specs()
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_complex=mc,
        model_simple=ms,
        model_name_prefix="u:abc/",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    models = payload["models"]["providers"]["litellm"]["models"]
    ids = {m["id"] for m in models}
    assert ids == {"u:abc/complex", "u:abc/simple"}
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
    mc, ms = _base_specs()
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
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_complex=mc,
        model_simple=ms,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    payload = json.loads(raw)
    by_id = {a["id"]: a for a in payload["agents"]["list"]}
    assert by_id["worker"]["model"] == f"litellm/{expected_suffix}"


def test_generate_openclaw_config_bootstrap_max_chars_in_defaults(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    mc, ms = _base_specs()
    raw = generate_openclaw_config(
        assembled_agents=_supervisor_only(make_assembled_agent),
        gateway_token="g",
        hooks_token="h",
        user_id=_USER_ID,
        sellerclaw_api_url="http://api",
        litellm_base_url="http://litellm",
        litellm_api_key="k",
        model_complex=mc,
        model_simple=ms,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_allowed_user_ids=(),
        telegram_allowed_group_ids=(),
    )
    assert (
        json.loads(raw)["agents"]["defaults"]["bootstrapMaxChars"] == OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS
    )


def test_generate_openclaw_config_web_search_enabled_without_provider_raises(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    mc, ms = _base_specs()
    with pytest.raises(ValueError, match="Web search provider"):
        generate_openclaw_config(
            assembled_agents=_supervisor_only(make_assembled_agent),
            gateway_token="g",
            hooks_token="h",
            user_id=_USER_ID,
            sellerclaw_api_url="http://api",
            litellm_base_url="http://litellm",
            litellm_api_key="k",
            model_complex=mc,
            model_simple=ms,
            telegram_enabled=False,
            telegram_bot_token="",
            telegram_allowed_user_ids=(),
            telegram_allowed_group_ids=(),
            web_search_enabled=True,
            web_search_provider=None,
            web_search_api_key="x",
        )
