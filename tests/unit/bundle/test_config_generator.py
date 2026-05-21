from __future__ import annotations

import json
from collections.abc import Callable, Sequence
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
    ModelDefaults,
    generate_openclaw_config,
)

pytestmark = pytest.mark.unit

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_AGENT_API_KEY = "test-sca-agent-key"


def _sample_providers() -> dict[str, object]:
    """A representative ``models.providers`` mapping passed into the generator.

    Mirrors a manifest-built shape: a LiteLLM virtual group (carries ``api``) plus
    native passthrough providers (no ``api``). The generator must emit this verbatim.
    """
    return {
        "litellm": {
            "baseUrl": "http://litellm",
            "apiKey": "k",
            "api": "openai-completions",
            "models": [
                {
                    "id": "u:abc/complex",
                    "name": "Frontier (auto)",
                    "reasoning": True,
                    "input": ["text", "image"],
                    "contextWindow": 128000,
                    "maxTokens": 8192,
                },
                {
                    "id": "u:abc/simple",
                    "name": "Mid (auto)",
                    "reasoning": False,
                    "input": ["text", "image"],
                    "contextWindow": 128000,
                    "maxTokens": 8192,
                },
            ],
        },
        "anthropic": {
            "baseUrl": "http://litellm/anthropic",
            "apiKey": "k",
            "models": [
                {
                    "id": "claude-sonnet-4-6",
                    "name": "Claude Sonnet 4.6",
                    "reasoning": False,
                    "input": ["text", "image"],
                    "contextWindow": 200000,
                    "maxTokens": 8192,
                }
            ],
        },
    }


def _sample_model_defaults() -> ModelDefaults:
    return ModelDefaults(
        model={"primary": "litellm/u:abc/complex"},
        compaction_model="litellm/u:abc/simple",
        memory_flush_model="litellm/u:abc/simple",
        image_generation_model={"primary": "litellm/u:abc/image"},
        video_generation_model={"primary": "google/veo-3.1-fast-generate-preview"},
        pdf_model={
            "primary": "anthropic/claude-sonnet-4-6",
            "fallbacks": ["google/gemini-3.1-pro-preview"],
        },
    )


def _generate(
    assembled_agents: Sequence[AssembledAgentConfig],
    *,
    providers: dict[str, object] | None = None,
    model_defaults: ModelDefaults | None = None,
    thinking_default: str = "adaptive",
    **kwargs: Any,
) -> str:
    """Call ``generate_openclaw_config`` with manifest-derived sample defaults.

    Centralizes the ``providers`` / ``model_defaults`` / ``thinking_default`` wiring so
    individual tests only override what they care about. Common flat params
    (gateway/hooks/user/api/telegram) default here too.
    """
    base: dict[str, Any] = {
        "gateway_token": "g",
        "hooks_token": "h",
        "agent_api_key": _AGENT_API_KEY,
        "user_id": _USER_ID,
        "sellerclaw_api_url": "http://api",
        "telegram_enabled": False,
        "telegram_bot_token": "",
        "telegram_allowed_user_ids": (),
        "telegram_allowed_group_ids": (),
    }
    base.update(kwargs)
    return generate_openclaw_config(
        assembled_agents=assembled_agents,
        providers=providers if providers is not None else _sample_providers(),
        model_defaults=model_defaults if model_defaults is not None else _sample_model_defaults(),
        thinking_default=thinking_default,
        **base,
    )


def _supervisor_only(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> list[AssembledAgentConfig]:
    return [
        make_assembled_agent(
            model_ref="litellm/u:abc/complex",
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
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        sellerclaw_api_url="http://api/",
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


def test_generate_openclaw_config_model_defaults_drive_and_omit_media(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        sellerclaw_api_url="http://api/",
        model_defaults=ModelDefaults(
            model={"primary": "litellm/p/complex"},
            compaction_model="litellm/p/simple",
            memory_flush_model="litellm/p/mini",
            image_generation_model=None,
            video_generation_model=None,
            pdf_model=None,
        ),
    )
    defaults = json.loads(raw)["agents"]["defaults"]
    assert defaults["model"] == {"primary": "litellm/p/complex"}
    assert defaults["compaction"]["model"] == "litellm/p/simple"
    assert defaults["compaction"]["memoryFlush"]["model"] == "litellm/p/mini"
    # None blocks are dropped entirely.
    assert "imageGenerationModel" not in defaults
    assert "videoGenerationModel" not in defaults
    assert "pdfModel" not in defaults


def test_generate_openclaw_config_models_providers_are_passed_verbatim(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """``models.providers`` is exactly the dict passed in — no hardcoded synthesis."""
    providers = _sample_providers()
    raw = _generate(_supervisor_only(make_assembled_agent), providers=providers)
    assert json.loads(raw)["models"]["providers"] == providers


def test_generate_openclaw_config_emits_group_api_only_when_present(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """A group carrying ``api`` keeps it; one without it omits the key entirely."""
    providers = _sample_providers()
    out = json.loads(_generate(_supervisor_only(make_assembled_agent), providers=providers))
    emitted = out["models"]["providers"]
    assert emitted["litellm"]["api"] == "openai-completions"
    assert "api" not in emitted["anthropic"]


def test_generate_openclaw_config_telegram_channel_and_bindings(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(
        _supervisor_only(make_assembled_agent),
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
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        sellerclaw_api_url="http://api/",
        sellerclaw_agent_api_base_url="http://api/agent",
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
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        sellerclaw_api_url="http://api/",
        web_search_enabled=True,
        web_search_auth_token="sca_test_token",
    )
    payload = json.loads(raw)
    entry = payload["plugins"]["entries"][SELLERCLAW_WEB_SEARCH_PLUGIN_ID]
    assert entry["config"]["webSearch"]["baseUrl"] == "http://api"


def test_generate_openclaw_config_web_search_disabled_has_no_plugin_or_provider(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(_supervisor_only(make_assembled_agent))
    payload = json.loads(raw)
    assert SELLERCLAW_WEB_SEARCH_PLUGIN_ID not in payload["plugins"]["allow"]
    assert SELLERCLAW_WEB_SEARCH_PLUGIN_ID not in payload["plugins"]["entries"]
    assert payload["tools"]["web"]["search"] == {"enabled": False}
    assert payload["plugins"]["load"]["paths"] == [OPENCLAW_PLUGIN_PATH_SELLERCLAW_UI]


def test_generate_openclaw_config_browser_disabled(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(_supervisor_only(make_assembled_agent), browser_enabled=False)
    assert json.loads(raw)["browser"]["enabled"] is False


def test_generate_openclaw_config_allowed_origins_in_control_ui(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(
        _supervisor_only(make_assembled_agent),
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


def test_generate_openclaw_config_litellm_model_ids_come_from_passed_providers(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Model ids/metadata are whatever the caller's ``providers`` carries — not synthesized."""
    raw = _generate(_supervisor_only(make_assembled_agent))
    models = json.loads(raw)["models"]["providers"]["litellm"]["models"]
    ids = {m["id"] for m in models}
    assert ids == {"u:abc/complex", "u:abc/simple"}
    by_id = {m["id"]: m for m in models}
    assert by_id["u:abc/complex"]["name"] == "Frontier (auto)"
    assert by_id["u:abc/complex"]["reasoning"] is True


@pytest.mark.parametrize(
    ("model_ref", "expected"),
    [
        pytest.param("litellm/u:abc/complex", "litellm/u:abc/complex", id="complex-ref"),
        pytest.param("litellm/u:abc/simple", "litellm/u:abc/simple", id="simple-ref"),
        pytest.param("anthropic/claude-sonnet-4-6", "anthropic/claude-sonnet-4-6", id="raw-ref"),
    ],
)
def test_generate_openclaw_config_agent_model_comes_from_model_ref(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    model_ref: str,
    expected: str,
) -> None:
    """The per-agent ``model`` is the assembled agent's ``model_ref`` verbatim — no tier map."""
    assembled = [
        make_assembled_agent(
            agent_id="worker",
            name="Worker",
            model_ref=model_ref,
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
            model_ref="litellm/u:abc/complex",
            subagent_ids=["worker"],
            tools_allow=["browser"],
            agents_md="# hi",
            memory_md="# m",
            soul_md=None,
            user_md=None,
            skills={},
        ),
    ]
    raw = _generate(assembled)
    payload = json.loads(raw)
    by_id = {a["id"]: a for a in payload["agents"]["list"]}
    assert by_id["worker"]["model"] == expected


def test_generate_openclaw_config_heartbeat_disabled_for_all_agents(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Heartbeat is disabled: no ``heartbeat`` block is emitted for any agent or in
    ``agents.defaults``. compaction / memory-flush still come from ``model_defaults``."""
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        model_defaults=ModelDefaults(
            model={"primary": "litellm/u:abc/complex"},
            compaction_model="litellm/u:abc/simple",
            memory_flush_model="litellm/u:abc/simple",
        ),
    )
    payload = json.loads(raw)
    defaults = payload["agents"]["defaults"]
    assert "heartbeat" not in defaults
    assert defaults["compaction"]["model"] == "litellm/u:abc/simple"
    assert defaults["compaction"]["memoryFlush"]["model"] == "litellm/u:abc/simple"
    for agent in payload["agents"]["list"]:
        assert "heartbeat" not in agent


@pytest.mark.parametrize(
    "thinking_default",
    [
        pytest.param("adaptive", id="adaptive"),
        pytest.param("off", id="off"),
        pytest.param("always", id="always"),
    ],
)
def test_generate_openclaw_config_thinking_default_reflects_passed_value(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    thinking_default: str,
) -> None:
    """``agents.defaults.thinkingDefault`` is exactly the passed value (manifest-driven)."""
    raw = _generate(_supervisor_only(make_assembled_agent), thinking_default=thinking_default)
    assert json.loads(raw)["agents"]["defaults"]["thinkingDefault"] == thinking_default


def test_generate_openclaw_config_per_agent_thinking_from_assembled_agent(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Per-agent ``thinkingDefault`` is emitted only when the assembled agent carries one."""
    assembled = [
        make_assembled_agent(
            agent_id="supplier",
            name="Supplier",
            model_ref="litellm/u:abc/simple",
            thinking_default="off",
            is_entry_point=False,
            subagent_ids=[],
            tools_allow=[],
            tools_deny=[],
            agents_md="# supplier",
            memory_md="# m-supplier",
            soul_md=None,
            user_md=None,
            skills={},
        ),
        make_assembled_agent(
            agent_id="scout",
            name="Scout",
            model_ref="litellm/u:abc/complex",
            thinking_default=None,
            is_entry_point=False,
            subagent_ids=[],
            tools_allow=[],
            tools_deny=[],
            agents_md="# scout",
            memory_md="# m-scout",
            soul_md=None,
            user_md=None,
            skills={},
        ),
        make_assembled_agent(
            model_ref="litellm/u:abc/complex",
            subagent_ids=["supplier", "scout"],
            tools_allow=["browser"],
            agents_md="# hi",
            memory_md="# m",
            soul_md=None,
            user_md=None,
            skills={},
        ),
    ]
    payload = json.loads(_generate(assembled))
    by_id = {a["id"]: a for a in payload["agents"]["list"]}
    assert by_id["supplier"]["thinkingDefault"] == "off"
    assert "thinkingDefault" not in by_id["scout"]
    assert "thinkingDefault" not in by_id["supervisor"]


def test_generate_openclaw_config_bootstrap_max_chars_in_defaults(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(_supervisor_only(make_assembled_agent))
    assert (
        json.loads(raw)["agents"]["defaults"]["bootstrapMaxChars"] == OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS
    )


def test_generate_openclaw_config_visible_replies_is_automatic(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Source replies must be ``automatic`` so the agent's prose is never suppressed.

    Regression guard: with the harness default (``message_tool_only``) OpenClaw drops the
    run's final assistant text on any turn that also used the ``message`` tool — the user
    saw the attached image but never the explanation. ``automatic`` keeps the prose visible.
    """
    raw = _generate(_supervisor_only(make_assembled_agent))
    assert json.loads(raw)["messages"]["visibleReplies"] == "automatic"


def test_generate_openclaw_config_queue_mode_is_followup(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """A follow-up sent while the agent is replying must be enqueued, not steered.

    Regression guard: OpenClaw's channel default ``steer`` folds the follow-up into the
    active run and returns no reply for its own turn, so the user gets a dropped/blank
    message. ``followup`` runs each message in order and never drops one.
    """
    raw = _generate(_supervisor_only(make_assembled_agent))
    assert json.loads(raw)["messages"]["queue"]["mode"] == "followup"


def test_generate_openclaw_config_web_search_enabled_requires_auth_token(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    with pytest.raises(ValueError, match="auth token"):
        _generate(
            _supervisor_only(make_assembled_agent),
            sellerclaw_api_url="http://api/",
            web_search_enabled=True,
            web_search_auth_token="",
        )


def test_generate_openclaw_config_web_search_enabled_requires_api_base_url(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    with pytest.raises(ValueError, match="SELLERCLAW_API_URL"):
        _generate(
            _supervisor_only(make_assembled_agent),
            sellerclaw_api_url="   ",
            web_search_enabled=True,
            web_search_auth_token="tok",
        )


def _generate_default_config(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    *,
    providers: dict[str, object] | None = None,
    model_defaults: ModelDefaults | None = None,
) -> Any:
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        providers=providers,
        model_defaults=model_defaults,
    )
    return json.loads(raw)


def test_generate_openclaw_config_media_defaults_emitted_from_model_defaults(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """image/video/pdf default blocks are the passed ``model_defaults`` values verbatim.

    (Derivation of the underlying refs is the bundle builder's job and covered there.)"""
    defaults = _generate_default_config(make_assembled_agent)["agents"]["defaults"]
    assert defaults["imageGenerationModel"] == {"primary": "litellm/u:abc/image"}
    assert defaults["videoGenerationModel"] == {"primary": "google/veo-3.1-fast-generate-preview"}
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
