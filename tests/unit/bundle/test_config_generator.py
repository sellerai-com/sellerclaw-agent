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
    OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID,
    OPENCLAW_LOCAL_AGENT_BASE_URL,
    OPENCLAW_PLUGIN_PATH_MEM0,
    OPENCLAW_BUNDLED_DIR_SELLERCLAW_UI,
    OPENCLAW_BUNDLED_DIR_SELLERCLAW_WEB_SEARCH,
    MEM0_PLUGIN_ID,
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
    # OpenClaw refuses to load a config whose `meta` block vanished while the previous
    # known-good file had one: it flags "missing-meta-vs-last-good" and silently restores
    # the previous backup. The block must therefore always be an object.
    assert payload["meta"] == {}
    assert payload["gateway"]["auth"]["token"] == "g"
    assert "litellm" in payload["models"]["providers"]
    assert set(payload["agents"]["entries"]) == {"supervisor"}
    assert payload["logging"]["level"] == OPENCLAW_BUNDLE_LOG_LEVEL
    assert payload["logging"]["consoleLevel"] == OPENCLAW_BUNDLE_LOG_LEVEL
    assert payload["logging"]["consoleStyle"] == OPENCLAW_BUNDLE_CONSOLE_STYLE
    # Secret redaction is no longer configurable (always on); the retired
    # `logging.redactSensitive` key would now be rejected as unknown.
    assert "redactSensitive" not in payload["logging"]
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
    # Path-loaded plugin: conversation-access hooks must be opted into, otherwise the
    # completion-delivery guard's answer capture and the reasoning relay (both ``llm_output`` /
    # ``agent_end``) are silently dropped at plugin load.
    assert (
        payload["plugins"]["entries"]["sellerclaw-ui"]["hooks"]["allowConversationAccess"] is True
    )


def test_generate_openclaw_config_stamps_runtime_version_in_meta(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """The stamp names the OpenClaw build shipped in this image — it drives OpenClaw's
    downgrade guard, so it is never invented and simply absent when the version is unknown."""
    payload = json.loads(
        _generate(_supervisor_only(make_assembled_agent), openclaw_version="2026.9.1-beta.1")
    )
    assert payload["meta"] == {"lastTouchedVersion": "2026.9.1-beta.1"}


@pytest.mark.parametrize(
    "version",
    [
        pytest.param(None, id="unknown"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
    ],
)
def test_generate_openclaw_config_meta_stays_an_object_without_version(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    version: str | None,
) -> None:
    """Dropping `meta` entirely would trip OpenClaw's `missing-meta-vs-last-good` guard and
    silently restore the previous config, so the block survives even with nothing to stamp."""
    payload = json.loads(_generate(_supervisor_only(make_assembled_agent), openclaw_version=version))
    assert payload["meta"] == {}


def test_generate_openclaw_config_agents_are_keyed_by_id_without_default_marker(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Agents live in a mapping keyed by id: no `id` field inside an entry, and the fleet
    marker replaces the retired per-agent `default` flag."""
    assembled = [
        make_assembled_agent(
            agent_id="scout",
            name="Scout",
            model_ref="litellm/u:abc/simple",
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
            subagent_ids=["scout"],
            tools_allow=["browser"],
            agents_md="# hi",
            memory_md="# m",
            soul_md=None,
            user_md=None,
            skills={},
        ),
    ]
    payload = json.loads(_generate(assembled))
    entries = payload["agents"]["entries"]
    assert set(entries) == {"scout", "supervisor"}
    assert entries["supervisor"]["name"] == "Supervisor"
    assert "id" not in entries["supervisor"]
    assert "default" not in entries["supervisor"]
    assert payload["agents"]["ownership"] == "explicit"
    defaults = payload["agents"]["defaults"]
    assert defaults["sessionStore"] == {"agentId": "supervisor"}
    assert defaults["systemAgent"] == {"agentId": "supervisor"}


def test_generate_openclaw_config_sole_agent_has_no_ownership_marker(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """A sole agent is its own implicit owner; stamping the fleet marker would make every
    ambient surface fail closed instead."""
    payload = json.loads(_generate(_supervisor_only(make_assembled_agent)))
    assert "ownership" not in payload["agents"]
    assert "sessionStore" not in payload["agents"]["defaults"]
    assert "systemAgent" not in payload["agents"]["defaults"]


def test_generate_openclaw_config_exec_runs_without_approval_prompts(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """`mode: "full"` replaces the retired `security` / `ask` pair.

    ``backgroundMs`` is pinned alongside it because OpenClaw's 10s default is shorter than the
    commands this agent runs all day — a CJ lookup, `catalog source-from-supplier`, a storefront
    publish — so each of them was detached into a background session and cost two extra turns of
    `process` polling. 60s holds them in the foreground; 120s is OpenClaw's ceiling.
    """
    payload = json.loads(_generate(_supervisor_only(make_assembled_agent)))
    assert payload["tools"]["exec"] == {"mode": "full", "backgroundMs": 60000}


def test_generate_openclaw_config_allowlists_only_healthcheck_bundled_skill(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Workspace/manifest skills stay; stock OpenClaw bundled skills are gated to healthcheck."""
    payload = json.loads(
        _generate(_supervisor_only(make_assembled_agent), sellerclaw_api_url="http://api/")
    )
    assert payload["skills"]["allowBundled"] == ["healthcheck"]


def test_generate_openclaw_config_disables_skill_workshop_autonomy(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Override OpenClaw's auto default so daily collection review does not run."""
    payload = json.loads(
        _generate(_supervisor_only(make_assembled_agent), sellerclaw_api_url="http://api/")
    )
    assert payload["skills"]["workshop"] == {"autonomous": {"mode": "off"}}


def test_generate_openclaw_config_cron_failures_redirect_to_cloud_error_sink(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Cron failures POST to the cloud error sink (webhook) instead of announcing in chat."""
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        sellerclaw_api_url="http://api/",
    )
    cron = json.loads(raw)["cron"]
    assert cron["enabled"] is True
    # Authenticated with the agent API key (cloud resolves the user via get_agent_user_id).
    assert cron["webhookToken"] == _AGENT_API_KEY
    # Every failed run must reach the sink: alerts on (default off), from the very first
    # failure (default: only after 2) and with no repeat cooldown (default: one hour).
    assert cron["failureAlert"] == {
        "enabled": True,
        "after": 1,
        "cooldownMs": 0,
        "mode": "webhook",
        "to": "http://api/internal/openclaw/errors",
    }


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


def test_generate_openclaw_config_whatsapp_channel_and_bindings(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        whatsapp_enabled=True,
        whatsapp_allowed_numbers=("+1 (415) 555-0123", "+14155550124"),
    )
    payload = json.loads(raw)
    whatsapp = payload["channels"]["whatsapp"]
    assert whatsapp["enabled"] is True
    assert whatsapp["dmPolicy"] == "allowlist"
    # Numbers are normalized to digits-only (OpenClaw matches allowFrom on digits).
    assert whatsapp["allowFrom"] == ["14155550123", "14155550124"]
    # DM-only: groups are hard-disabled and no group fields are emitted.
    assert whatsapp["groupPolicy"] == "disabled"
    assert "groups" not in whatsapp
    bindings = payload["bindings"]
    assert any(b.get("match") == {"channel": "whatsapp"} for b in bindings)


def test_generate_openclaw_config_whatsapp_open_policy_without_allowlist(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        whatsapp_enabled=True,
        whatsapp_allowed_numbers=(),
    )
    payload = json.loads(raw)
    whatsapp = payload["channels"]["whatsapp"]
    assert whatsapp["dmPolicy"] == "open"
    assert whatsapp["allowFrom"] == []


def test_generate_openclaw_config_whatsapp_absent_when_disabled(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(_supervisor_only(make_assembled_agent), whatsapp_enabled=False)
    payload = json.loads(raw)
    assert "whatsapp" not in payload["channels"]
    assert not any(b.get("match") == {"channel": "whatsapp"} for b in payload["bindings"])


def test_generate_openclaw_config_channel_plugins_stay_out_of_allow(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Allow-listing whatsapp makes an unpaired channel block gateway readiness: /ready answers
    503 with failing=["whatsapp"] until someone scans a QR, and the cloud reads the agent as still
    starting. The runtime auto-enables the plugin for the boot anyway, so the config leaves it out.
    """
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        telegram_enabled=True,
        telegram_bot_token="bot-secret",
        whatsapp_enabled=True,
        whatsapp_allowed_numbers=("+14155550123",),
    )
    payload = json.loads(raw)
    allow = payload["plugins"]["allow"]
    assert payload["channels"]["whatsapp"]["enabled"] is True
    assert payload["channels"]["telegram"]["enabled"] is True
    assert "whatsapp" not in allow
    assert "telegram" not in allow


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
    # Both of our plugins are bundled into the image, so neither may be on a load path.
    assert payload["plugins"]["load"]["paths"] == []
    assert OPENCLAW_BUNDLED_DIR_SELLERCLAW_UI not in payload["plugins"]["load"]["paths"]
    assert OPENCLAW_BUNDLED_DIR_SELLERCLAW_WEB_SEARCH not in payload["plugins"]["load"]["paths"]


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
    assert payload["plugins"]["load"]["paths"] == []


def test_generate_openclaw_config_memory_enabled_wires_mem0_platform_plugin(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        sellerclaw_api_url="http://api/",
        sellerclaw_agent_api_base_url="http://api/agent",
        memory_enabled=True,
    )
    payload = json.loads(raw)
    # Memory slot + entry point at the cloud Mem0-compatible adapter in PLATFORM mode.
    assert payload["plugins"]["slots"]["memory"] == MEM0_PLUGIN_ID
    assert MEM0_PLUGIN_ID in payload["plugins"]["allow"]
    # The load path is REQUIRED: plugins.slots.memory resolves against load.paths + the installed
    # registry, so without it config validation hard-fails ("plugin not found: openclaw-mem0") and
    # the gateway refuses to boot. Regression guard for exactly that outage.
    assert OPENCLAW_PLUGIN_PATH_MEM0 in payload["plugins"]["load"]["paths"]
    entry = payload["plugins"]["entries"][MEM0_PLUGIN_ID]
    assert entry["config"]["mode"] == "platform"
    # baseUrl = agent API base + /mem0; apiKey = the agent's own token (cloud bills the user).
    assert entry["config"]["baseUrl"] == "http://api/agent/mem0"
    assert entry["config"]["apiKey"] == _AGENT_API_KEY
    assert entry["config"]["userId"] == str(_USER_ID)
    assert entry["config"]["skills"]["recall"]["enabled"] is True
    # Dream (periodic in-conversation consolidation) is off — capture is done cloud-side instead.
    assert entry["config"]["skills"]["dream"]["enabled"] is False
    # Path-loaded (non-bundled) plugin: opt into conversation-access hooks so the agent_end
    # auto-capture hook isn't silently blocked (otherwise nothing is ever written to memory).
    assert entry["hooks"]["allowConversationAccess"] is True


def test_generate_openclaw_config_memory_disabled_has_no_mem0_plugin_or_slot(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(_supervisor_only(make_assembled_agent))
    payload = json.loads(raw)
    assert "slots" not in payload["plugins"]
    assert MEM0_PLUGIN_ID not in payload["plugins"]["allow"]
    assert MEM0_PLUGIN_ID not in payload["plugins"]["entries"]
    assert OPENCLAW_PLUGIN_PATH_MEM0 not in payload["plugins"]["load"]["paths"]


def test_generate_openclaw_config_browser_disabled(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    raw = _generate(_supervisor_only(make_assembled_agent), browser_enabled=False)
    assert json.loads(raw)["browser"]["enabled"] is False


def test_generate_openclaw_config_browser_hardening_and_media_ttl(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    payload = json.loads(_generate(_supervisor_only(make_assembled_agent)))
    assert payload["browser"]["evaluateEnabled"] is False
    assert payload["browser"]["snapshotDefaults"] == {"mode": "efficient"}
    # Stored media TTL lives under `attachments` (the retired root `media` block).
    assert payload["attachments"]["ttlHours"] == 168
    assert "media" not in payload


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


def test_generate_openclaw_config_trusts_only_the_local_nginx_hop(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Nothing wider than the nginx hop may be listed, or host callers lose their identity.

    OpenClaw resolves the real client by walking ``X-Forwarded-For`` from the right and
    discarding every entry that is itself a trusted proxy. Listing the docker bridge range
    discarded the address of a caller coming from the host, leaving no client to attribute,
    and every gateway-authenticated request through nginx was refused with
    ``proxy_attribution_required``.
    """
    payload = json.loads(_generate(_supervisor_only(make_assembled_agent)))
    assert payload["gateway"]["trustedProxies"] == ["127.0.0.1", "::1"]


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
    assert payload["agents"]["entries"]["worker"]["model"] == expected


def test_generate_openclaw_config_heartbeat_disabled_for_all_agents(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Heartbeat is disabled via an explicit ``agents.defaults.heartbeat.every = "0m"`` —
    OpenClaw's default is an enabled 30m/1h poll, so omitting the block would leave it running.
    It also pins the cheap ``simple`` tier (memory-flush group) as a cost guard in case heartbeat
    is ever re-enabled. No per-agent heartbeat block is emitted. compaction / memory-flush still
    come from ``model_defaults``."""
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
    assert defaults["heartbeat"] == {"every": "0m", "model": "litellm/u:abc/simple"}
    assert defaults["compaction"]["model"] == "litellm/u:abc/simple"
    assert defaults["compaction"]["memoryFlush"]["model"] == "litellm/u:abc/simple"
    for agent in payload["agents"]["entries"].values():
        assert "heartbeat" not in agent


def test_generate_openclaw_config_heartbeat_cadence_is_cloud_owned(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """The cadence comes from the manifest (``heartbeat_every``); the model stays pinned to the
    cheap simple tier no matter the cadence, so an enabled heartbeat never burns the primary."""
    raw = _generate(
        _supervisor_only(make_assembled_agent),
        heartbeat_every="30m",
        model_defaults=ModelDefaults(
            model={"primary": "litellm/u:abc/complex"},
            compaction_model="litellm/u:abc/simple",
            memory_flush_model="litellm/u:abc/simple",
        ),
    )
    defaults = json.loads(raw)["agents"]["defaults"]
    assert defaults["heartbeat"] == {"every": "30m", "model": "litellm/u:abc/simple"}


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


@pytest.mark.parametrize(
    "reasoning_default",
    [
        pytest.param("on", id="on"),
        pytest.param("off", id="off"),
        pytest.param("stream", id="stream"),
    ],
)
def test_generate_openclaw_config_reasoning_default_reflects_passed_value(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
    reasoning_default: str,
) -> None:
    """``agents.defaults.reasoningDefault`` is exactly the passed value (manifest-driven)."""
    raw = _generate(_supervisor_only(make_assembled_agent), reasoning_default=reasoning_default)
    assert json.loads(raw)["agents"]["defaults"]["reasoningDefault"] == reasoning_default


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
    by_id = payload["agents"]["entries"]
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


def test_generate_openclaw_config_streams_replies_while_the_agent_works(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """The owner should see the agent talking mid-run, not one lump when the turn ends.

    ``text_end`` pushes each finished text block before the agent picks up a tool, and the
    chunk floor decides when a long stretch is cut. At 800 a six-minute run whose running
    commentary totalled 671 characters showed nothing until it was done.
    """
    defaults = json.loads(_generate(_supervisor_only(make_assembled_agent)))["agents"]["defaults"]

    assert defaults["blockStreamingDefault"] == "on"
    assert defaults["blockStreamingBreak"] == "text_end"
    assert defaults["blockStreamingChunk"]["minChars"] == 200
    # A cut must still land on a line break rather than mid-sentence.
    assert defaults["blockStreamingChunk"]["breakPreference"] == "newline"
    assert defaults["blockStreamingChunk"]["maxChars"] == 3000


def test_generate_openclaw_config_subagent_run_timeout_in_defaults(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Three distinct timeouts: a per-turn cap, a total-run cap, and the announce window.

    ``timeoutSeconds`` bounds a single agent turn; ``subagents.runTimeoutSeconds`` bounds a
    spawned subagent's whole delegated run (one hour), so a stuck delegation cannot run forever.
    ``subagents.announceTimeoutMs`` is how long OpenClaw waits for the supervisor's completion
    run before steering a duplicate of the completion event into the still-running session —
    the 120s default fired mid-run on a healthy announce and the owner's report was silently
    dropped (NO_REPLY became the run's final). Must stay above ``timeoutSeconds`` so a hung
    turn is aborted by its own cap before the announce window ever binds.
    See https://docs.openclaw.ai/tools/subagents.

    The turn cap was raised 600 -> 1200 after staging chat b76fd17a (2026-08-19), where three
    turns of ordinary supervisor work were cut at exactly 600s and the owner saw "LLM request
    timed out." instead of finished work. It must stay below the total-run cap so a delegated
    subagent still gets more than one turn, and below the gateway proxy read timeout (1800s).
    """
    defaults = json.loads(_generate(_supervisor_only(make_assembled_agent)))["agents"]["defaults"]
    assert defaults["timeoutSeconds"] == 1200
    assert defaults["subagents"] == {"runTimeoutSeconds": 3600, "announceTimeoutMs": 1_230_000}
    assert defaults["subagents"]["announceTimeoutMs"] > defaults["timeoutSeconds"] * 1000
    # A subagent's whole run must still fit more than one turn, or the total-run cap would
    # effectively become a per-turn cap.
    assert defaults["subagents"]["runTimeoutSeconds"] > defaults["timeoutSeconds"] * 2


def test_generate_openclaw_config_suppresses_tool_error_warnings(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """A failed tool call must never reach the user as an engine-synthesized notice.

    The agent sees the error in its own context and normally recovers on the next call, so
    "⚠️ <Tool> failed: …" only reads as a product bug. Cloud-side it is worse than noise: it
    arrives on the final road after the agent's own reply was streamed as preview blocks, and
    committing it drops that streamed text — the user is left with the notice instead of the
    answer.
    """
    messages = json.loads(_generate(_supervisor_only(make_assembled_agent)))["messages"]
    assert messages["suppressToolErrors"] is True
    assert messages["visibleReplies"] == "automatic"


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


def test_generate_openclaw_config_omits_media_model_blocks_keeps_pdf(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """image/video model blocks are NOT emitted (media goes via sellerclaw-cli); pdfModel stays.

    (The LiteLLM {user}image / {user}video groups remain registered cloud-side.)"""
    defaults = _generate_default_config(make_assembled_agent)["agents"]["defaults"]
    assert "imageGenerationModel" not in defaults
    assert "videoGenerationModel" not in defaults
    assert defaults["pdfModel"] == {
        "primary": "anthropic/claude-sonnet-4-6",
        "fallbacks": ["google/gemini-3.1-pro-preview"],
    }


def test_generate_openclaw_config_denies_builtin_media_tools(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """Builtin image/video/music generate tools are denied (replaced by sellerclaw-cli media)."""
    entries = json.loads(_generate(_supervisor_only(make_assembled_agent)))["agents"]["entries"]
    deny = entries["supervisor"]["tools"]["deny"]
    assert "image_generate" in deny
    assert "video_generate" in deny
    assert "music_generate" in deny


def test_generate_openclaw_config_enables_document_extract_plugin_for_pdf_fallback(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    """OpenClaw's PDF tool fails with `enable the document-extract plugin` when the
    bundled extractor isn't enabled — must always be in `plugins.allow` AND `plugins.entries`."""
    payload = _generate_default_config(make_assembled_agent)
    plugins = payload["plugins"]
    assert OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID in plugins["allow"]
    assert plugins["entries"][OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID] == {"enabled": True}
