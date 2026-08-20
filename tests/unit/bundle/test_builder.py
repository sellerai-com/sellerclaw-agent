from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sellerclaw_agent.bundle.builder import BundleBuilder, derive_agent_tools
from sellerclaw_agent.bundle.manifest import GenericManifest, bundle_manifest_from_mapping
from sellerclaw_agent.test_manifest_fixtures import load_manifest_v2_mapping

pytestmark = pytest.mark.unit

_GW = "gw"
_HOOKS = "hooks"

# Long-term memory tools granted to every agent (mem0 plugin). Hardcoded here — NOT imported from
# builder — so a typo or reordering in the implementation is caught by the exact-equality assertion.
_MEMORY_TOOLS_EXPECTED = [
    "memory_search",
    "memory_add",
    "memory_get",
    "memory_list",
    "memory_update",
    "memory_delete",
]


def _config_from_llm(mutate_llm: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Build the OpenClaw config from the v2 fixture, optionally mutating its ``llm`` block."""
    mapping = copy.deepcopy(load_manifest_v2_mapping())
    if mutate_llm is not None:
        mutate_llm(mapping["llm"])
    manifest = bundle_manifest_from_mapping(mapping)
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS, agent_api_key="k")
    return json.loads(result.openclaw_config)


def _agent_payload(cfg: dict, agent_id: str) -> dict:
    entries = cfg["agents"]["entries"]
    if agent_id not in entries:
        raise AssertionError(f"agent {agent_id!r} not in config")
    return entries[agent_id]


def test_bundle_builder_produces_config_and_workspaces(
    make_manifest: Callable[..., GenericManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    assert '"gateway"' in result.openclaw_config
    assert "supervisor/AGENTS.md" in result.workspaces
    assert len(result.version) == 64
    assert result.shared_skills == {}
    assert "supervisor/skills/task-management/SKILL.md" in result.workspaces
    assert "supervisor/skills/tasks/SKILL.md" in result.workspaces


def test_bundle_builder_includes_subagent_workspaces(
    make_manifest: Callable[..., GenericManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    assert "scout/AGENTS.md" in result.workspaces
    assert "scout/MEMORY.md" in result.workspaces
    assert "supplier/AGENTS.md" in result.workspaces
    assert "marketing/AGENTS.md" in result.workspaces
    # Subagent skills are embedded under the subagent workspace.
    assert "scout/skills/trend-analysis/SKILL.md" in result.workspaces


def test_defaults_models_derived_from_manifest_llm() -> None:
    defaults = _config_from_llm()["agents"]["defaults"]
    assert defaults["model"] == {"primary": "litellm/u:5fdc144e/complex"}
    # image/video model blocks are no longer emitted (media goes via sellerclaw-cli; builtin
    # image_generate/video_generate are denied). The LiteLLM groups stay registered cloud-side.
    assert "imageGenerationModel" not in defaults
    assert "videoGenerationModel" not in defaults
    assert defaults["pdfModel"] == {
        "primary": "anthropic/claude-sonnet-4-6",
        "fallbacks": ["google/gemini-3.1-pro-preview"],
    }
    assert defaults["compaction"]["model"] == "litellm/u:5fdc144e/simple"
    assert defaults["compaction"]["memoryFlush"]["model"] == "litellm/u:5fdc144e/simple"


def test_defaults_omit_media_models_when_manifest_omits_them() -> None:
    def _drop(llm: dict[str, Any]) -> None:
        llm.pop("image_model")
        llm.pop("video_model")
        llm.pop("pdf_model")

    defaults = _config_from_llm(_drop)["agents"]["defaults"]
    assert "imageGenerationModel" not in defaults
    assert "videoGenerationModel" not in defaults
    assert "pdfModel" not in defaults
    # text_model.primary -> model stays present.
    assert defaults["model"] == {"primary": "litellm/u:5fdc144e/complex"}


def test_compaction_falls_back_to_text_primary_when_manifest_omits() -> None:
    def _drop(llm: dict[str, Any]) -> None:
        llm.pop("compaction_model")
        llm.pop("memory_flush_model")

    compaction = _config_from_llm(_drop)["agents"]["defaults"]["compaction"]
    assert compaction["model"] == "litellm/u:5fdc144e/complex"
    assert compaction["memoryFlush"]["model"] == "litellm/u:5fdc144e/complex"


def test_providers_built_from_manifest_groups() -> None:
    """``models.providers`` is built strictly from the manifest groups: the litellm
    virtual group carries ``api`` + prefixed ids + manifest metadata; the native
    passthrough providers keep their own (independent) baseUrls and omit ``api``."""
    providers = _config_from_llm()["models"]["providers"]

    litellm = providers["litellm"]
    assert litellm["baseUrl"] == "https://example.ngrok-free.dev/litellm"
    assert litellm["apiKey"] == "sk-EXAMPLEKEY"
    assert litellm["api"] == "openai-completions"
    by_id = {m["id"]: m for m in litellm["models"]}
    # image/video virtual groups are NOT rendered into the config (media goes via
    # sellerclaw-cli); they remain registered in LiteLLM cloud-side.
    assert set(by_id) == {
        "u:5fdc144e/complex",
        "u:5fdc144e/simple",
        "u:5fdc144e/mini",
    }
    assert by_id["u:5fdc144e/complex"] == {
        "id": "u:5fdc144e/complex",
        "name": "Frontier (auto)",
        "input": ["text", "image"],
        "reasoning": True,
        "contextWindow": 256000,
        "maxTokens": 32768,
    }
    # Non-complex litellm models omit reasoning/contextWindow/maxTokens (OpenClaw defaults).
    for non_complex in (
        "u:5fdc144e/simple",
        "u:5fdc144e/mini",
    ):
        entry = by_id[non_complex]
        assert "reasoning" not in entry
        assert "contextWindow" not in entry
        assert "maxTokens" not in entry

    anthropic = providers["anthropic"]
    assert anthropic["baseUrl"] == "https://example.ngrok-free.dev/litellm/anthropic"
    assert anthropic["apiKey"] == "sk-EXAMPLEKEY"
    assert "api" not in anthropic
    assert anthropic["models"] == [
        {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "input": ["text", "image"],
        }
    ]

    google = providers["google"]
    assert google["baseUrl"] == "https://example.ngrok-free.dev/litellm/gemini"
    assert "api" not in google
    # veo (the video model) is filtered out; only the PDF model (gemini) remains.
    assert {m["id"] for m in google["models"]} == {"gemini-3.1-pro-preview"}


def test_per_agent_model_from_manifest_text_refs_and_heartbeat_disabled() -> None:
    """Per-agent ``model`` is the manifest text ref for the agent's role; heartbeat is
    disabled for every agent (no ``heartbeat`` block emitted)."""
    cfg = _config_from_llm()
    assert _agent_payload(cfg, "supervisor")["model"] == "litellm/u:5fdc144e/complex"
    assert _agent_payload(cfg, "scout")["model"] == "litellm/u:5fdc144e/complex"
    assert _agent_payload(cfg, "marketing")["model"] == "litellm/u:5fdc144e/complex"
    assert _agent_payload(cfg, "supplier")["model"] == "litellm/u:5fdc144e/simple"

    for agent in cfg["agents"]["entries"].values():
        assert "heartbeat" not in agent


def test_defaults_thinking_default_from_manifest() -> None:
    assert _config_from_llm()["agents"]["defaults"]["thinkingDefault"] == "adaptive"

    mapping = copy.deepcopy(load_manifest_v2_mapping())
    mapping["agents"]["thinking_default"] = "off"
    manifest = bundle_manifest_from_mapping(mapping)
    cfg = json.loads(
        BundleBuilder()
        .build(manifest, gateway_token=_GW, hooks_token=_HOOKS, agent_api_key="k")
        .openclaw_config
    )
    assert cfg["agents"]["defaults"]["thinkingDefault"] == "off"


def test_defaults_reasoning_default_from_manifest() -> None:
    # Fixture carries reasoning_default="on".
    assert _config_from_llm()["agents"]["defaults"]["reasoningDefault"] == "on"

    # Absent in the manifest -> OpenClaw's own default ("off").
    mapping = copy.deepcopy(load_manifest_v2_mapping())
    mapping["agents"].pop("reasoning_default", None)
    cfg = _cfg_from_mapping(mapping)
    assert cfg["agents"]["defaults"]["reasoningDefault"] == "off"


def test_providers_are_manifest_driven_not_hardcoded() -> None:
    """Mutating a group's base_url + a model's metadata in the mapping must flow straight
    into ``models.providers`` — proving the block is manifest-driven, not synthesized."""

    def _mutate(llm: dict[str, Any]) -> None:
        llm["groups"]["anthropic"]["base_url"] = "https://mutated.example/anthropic"
        llm["groups"]["litellm"]["models"][0]["name"] = "Mutated Frontier"
        llm["groups"]["litellm"]["models"][0]["contextWindow"] = 999999

    providers = _config_from_llm(_mutate)["models"]["providers"]
    assert providers["anthropic"]["baseUrl"] == "https://mutated.example/anthropic"
    complex_model = next(
        m for m in providers["litellm"]["models"] if m["id"] == "u:5fdc144e/complex"
    )
    assert complex_model["name"] == "Mutated Frontier"
    assert complex_model["contextWindow"] == 999999


def test_bundle_builder_entry_point_tools_and_subagents(
    make_manifest: Callable[..., GenericManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    cfg = json.loads(result.openclaw_config)
    supervisor = _agent_payload(cfg, "supervisor")
    # The retired per-agent `default` marker is gone: in a fleet every surface resolves its
    # owner explicitly, so the entry point is designated by the channel bindings instead.
    assert "default" not in supervisor
    assert cfg["agents"]["ownership"] == "explicit"
    assert {"agentId": "supervisor", "match": {"channel": "sellerclaw-ui"}} in cfg["bindings"]
    assert "group:sessions" in supervisor["tools"]["allow"]
    assert supervisor["subagents"]["allowAgents"] == ["scout", "supplier", "marketing"]
    # The roster (`allowAgents`) is emitted ONLY for the entry point. `defaults.subagents` carries
    # the shared run-timeout cap and must never name agents.
    assert "allowAgents" not in cfg["agents"]["defaults"]["subagents"]
    for sub in ("scout", "supplier", "marketing"):
        assert "subagents" not in _agent_payload(cfg, sub)


def test_bundle_builder_entry_point_keeps_delegation_tools_with_empty_team(
    make_manifest: Callable[..., GenericManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team-less supervisor still gets ``group:sessions`` + ``agents_list``.

    Regression: these used to be granted only when the manifest carried at least one subagent.
    OpenClaw hot-applies a changed ``allowAgents`` roster to the running gateway but never
    re-derives a changed tool allow-list, so an agent that started with an empty team stayed
    unable to list or spawn anyone — through a restart of the *chat*, not just the current
    session. Enabling the first specialist therefore did nothing until the whole agent was
    restarted. The roster is the gate (empty here); the tools are constant.
    """
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    mapping = load_manifest_v2_mapping()
    mapping["agents"] = {**mapping["agents"], "subagents": []}
    manifest = make_manifest(overrides={"agents": mapping["agents"]})
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    cfg = json.loads(result.openclaw_config)
    supervisor = _agent_payload(cfg, "supervisor")
    assert "group:sessions" in supervisor["tools"]["allow"]
    assert "agents_list" in supervisor["tools"]["allow"]
    # Nobody to delegate to yet — and `requireAgentId` leaves no id `sessions_spawn` accepts,
    # so the granted tools cannot spawn a clone of the supervisor either.
    assert supervisor["subagents"] == {"allowAgents": [], "requireAgentId": True}
    assert list(cfg["agents"]["entries"]) == ["supervisor"]
    # A sole agent stays its own implicit owner, so the fleet marker must not appear.
    assert "ownership" not in cfg["agents"]


def test_bundle_builder_supplier_thinking_off_from_manifest(
    make_manifest: Callable[..., GenericManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    cfg = json.loads(result.openclaw_config)
    supplier = _agent_payload(cfg, "supplier")
    assert supplier["thinkingDefault"] == "off"


def test_bundle_builder_browser_disabled_removes_browser_tool(
    make_manifest: Callable[..., GenericManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supplier subagent has browser_enabled:false in the fixture."""
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    cfg = json.loads(result.openclaw_config)
    supplier = _agent_payload(cfg, "supplier")
    assert "browser" not in supplier["tools"]["allow"]
    scout = _agent_payload(cfg, "scout")
    assert "browser" in scout["tools"]["allow"]


def test_bundle_builder_created_at_override(
    make_manifest: Callable[..., GenericManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    fixed = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    result = BundleBuilder().build(
        manifest,
        gateway_token=_GW,
        hooks_token=_HOOKS,
        created_at=fixed,
    )
    assert result.created_at == fixed


def test_bundle_builder_requires_agent_api_key(
    make_manifest: Callable[..., GenericManifest],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    manifest = make_manifest()
    with pytest.raises(ValueError, match="agent API key is required"):
        BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS, data_dir=tmp_path)


def test_bundle_builder_web_search_enabled_uses_agent_api_key_env(
    make_manifest: Callable[..., GenericManifest],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "sca_from_env")
    monkeypatch.setattr(
        "sellerclaw_agent.bundle.builder.get_sellerclaw_api_url",
        lambda: "https://sellerclaw.example",
    )
    manifest = make_manifest(web_search_enabled=True)
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS, data_dir=tmp_path)
    cfg = json.loads(result.openclaw_config)
    web_search_cfg = cfg["plugins"]["entries"]["sellerclaw-web-search"]["config"]["webSearch"]
    assert web_search_cfg["authToken"] == "sca_from_env"
    # Derived SELLERCLAW_AGENT_API_BASE_URL = SELLERCLAW_API_URL + agent_api_base_path.
    # The plugin baseUrl must already include the agent prefix so its ``/research/web-search``
    # call resolves to ``/agent/research/web-search`` on the monolith.
    assert web_search_cfg["baseUrl"] == "https://sellerclaw.example/agent"


def test_bundle_builder_web_search_baseurl_respects_manifest_agent_api_base_path(
    make_manifest: Callable[..., GenericManifest],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The web-search plugin baseUrl must follow the manifest-supplied path segment."""
    monkeypatch.setenv("AGENT_API_KEY", "sca_from_env")
    monkeypatch.setattr(
        "sellerclaw_agent.bundle.builder.get_sellerclaw_api_url",
        lambda: "https://api.example.com",
    )
    manifest = make_manifest(web_search_enabled=True, agent_api_base_path="/custom/agent")
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS, data_dir=tmp_path)
    cfg = json.loads(result.openclaw_config)
    web_search_cfg = cfg["plugins"]["entries"]["sellerclaw-web-search"]["config"]["webSearch"]
    assert web_search_cfg["baseUrl"] == "https://api.example.com/custom/agent"


def test_bundle_builder_web_search_enabled_requires_sellerclaw_api_url(
    make_manifest: Callable[..., GenericManifest],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "sca_from_env")
    monkeypatch.setattr("sellerclaw_agent.bundle.builder.get_sellerclaw_api_url", lambda: "   ")
    manifest = make_manifest(web_search_enabled=True)
    with pytest.raises(ValueError, match="SELLERCLAW_API_URL"):
        BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS, data_dir=tmp_path)


@pytest.mark.parametrize(
    ("sellerclaw_api_url", "agent_api_base_path", "expected"),
    [
        pytest.param(
            "https://api.example.com",
            "/agent",
            "https://api.example.com/agent",
            id="standard-agent-prefix",
        ),
        pytest.param(
            "https://api.example.com/",
            "/agent",
            "https://api.example.com/agent",
            id="trailing-slash-trimmed",
        ),
        pytest.param(
            "https://api.example.com",
            "",
            "https://api.example.com",
            id="empty-path-yields-bare-host",
        ),
        pytest.param(
            "",
            "/agent",
            "",
            id="empty-host-yields-empty-url",
        ),
        pytest.param(
            "https://api.example.com",
            "/custom/agent/",
            "https://api.example.com/custom/agent",
            id="nested-path-trailing-slash-trimmed",
        ),
    ],
)
def test_compose_agent_api_base_url_concatenates_host_and_path(
    sellerclaw_api_url: str,
    agent_api_base_path: str,
    expected: str,
) -> None:
    from sellerclaw_agent.bundle.builder import _compose_agent_api_base_url

    assert (
        _compose_agent_api_base_url(
            sellerclaw_api_url=sellerclaw_api_url,
            agent_api_base_path=agent_api_base_path,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("is_entry_point", "browser", "cron", "expected_allow", "expected_deny"),
    [
        pytest.param(
            True, True, True,
            [
                "group:sessions", "agents_list", "group:fs", "group:web", "web_search",
                "message", "browser", "exec", "pdf", "cron", "process", *_MEMORY_TOOLS_EXPECTED,
            ],
            [],
            id="entry-point",
        ),
        pytest.param(
            True, True, False,
            [
                "group:sessions", "agents_list", "group:fs", "group:web", "web_search",
                "message", "browser", "exec", "pdf", "process", *_MEMORY_TOOLS_EXPECTED,
            ],
            [],
            id="entry-point-cron-disabled",
        ),
        pytest.param(
            False, True, True,
            [
                "group:fs", "exec", "process", "web_fetch", "web_search", "browser", "pdf",
                *_MEMORY_TOOLS_EXPECTED,
            ],
            ["group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"],
            id="subagent-never-gets-cron",
        ),
        pytest.param(
            False, False, True,
            [
                "group:fs", "exec", "process", "web_fetch", "web_search", "pdf",
                *_MEMORY_TOOLS_EXPECTED,
            ],
            ["group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"],
            id="subagent-browser-disabled",
        ),
    ],
)
def test_derive_agent_tools(
    is_entry_point: bool,
    browser: bool,
    cron: bool,
    expected_allow: list[str],
    expected_deny: list[str],
) -> None:
    allow, deny = derive_agent_tools(
        is_entry_point=is_entry_point,
        browser_enabled=browser,
        cron_enabled=cron,
    )
    assert allow == expected_allow
    assert deny == expected_deny
    # The builtin media tools are never granted (media goes through sellerclaw-cli).
    assert "image_generate" not in allow
    assert "video_generate" not in allow


def test_derive_agent_tools_grants_whatsapp_login_to_entry_point_when_enabled() -> None:
    """The entry point gets the in-chat ``whatsapp_login`` QR tool only when WhatsApp is on."""
    allow, _ = derive_agent_tools(
        is_entry_point=True,
        browser_enabled=True,
        cron_enabled=False,
        whatsapp_enabled=True,
    )
    assert "whatsapp_login" in allow


def test_derive_agent_tools_omits_whatsapp_login_when_disabled() -> None:
    allow, _ = derive_agent_tools(
        is_entry_point=True,
        browser_enabled=True,
        cron_enabled=False,
        whatsapp_enabled=False,
    )
    assert "whatsapp_login" not in allow


def test_derive_agent_tools_never_grants_whatsapp_login_to_subagent() -> None:
    """Subagents never pair WhatsApp, even if the flag is set."""
    allow, _ = derive_agent_tools(
        is_entry_point=False,
        browser_enabled=True,
        cron_enabled=True,
        whatsapp_enabled=True,
    )
    assert "whatsapp_login" not in allow


@pytest.mark.parametrize(
    "is_entry_point",
    [
        pytest.param(True, id="entry-point"),
        pytest.param(False, id="subagent"),
    ],
)
def test_derive_agent_tools_grants_memory_tools_to_every_agent(is_entry_point: bool) -> None:
    """Both the supervisor and subagents get the mem0 read/write tools, never in ``deny``.

    Without these the injected triage protocol tells the agent to call ``memory_add`` but no such
    tool exists, so durable facts are acknowledged ("Saved!") yet never persisted.
    """
    allow, deny = derive_agent_tools(
        is_entry_point=is_entry_point,
        browser_enabled=True,
        cron_enabled=True,
    )
    for tool in ("memory_add", "memory_search", "memory_update", "memory_delete"):
        assert tool in allow
        assert tool not in deny


def _cfg_from_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    manifest = bundle_manifest_from_mapping(mapping)
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS, agent_api_key="k")
    return json.loads(result.openclaw_config)


def test_cron_enabled_from_manifest_and_supervisor_only_tool() -> None:
    cfg = _cfg_from_mapping(copy.deepcopy(load_manifest_v2_mapping()))  # default -> enabled
    assert cfg["cron"]["enabled"] is True
    assert "cron" in _agent_payload(cfg, "supervisor")["tools"]["allow"]
    for sub in ("scout", "supplier", "marketing"):
        assert "cron" not in _agent_payload(cfg, sub)["tools"]["allow"]
        assert "cron" in _agent_payload(cfg, sub)["tools"]["deny"]

    off = copy.deepcopy(load_manifest_v2_mapping())
    off["cron"] = {"enabled": False}
    cfg_off = _cfg_from_mapping(off)
    assert cfg_off["cron"]["enabled"] is False
    assert "cron" not in _agent_payload(cfg_off, "supervisor")["tools"]["allow"]


def test_web_fetch_enabled_from_manifest() -> None:
    assert _cfg_from_mapping(copy.deepcopy(load_manifest_v2_mapping()))["tools"]["web"]["fetch"]["enabled"] is True
    off = copy.deepcopy(load_manifest_v2_mapping())
    off["web_fetch"] = {"enabled": False}
    assert _cfg_from_mapping(off)["tools"]["web"]["fetch"]["enabled"] is False


def test_telegram_channel_enabled_from_manifest() -> None:
    cfg = _cfg_from_mapping(copy.deepcopy(load_manifest_v2_mapping()))  # primary=telegram, enabled
    assert cfg["channels"]["telegram"]["enabled"] is True
    # Token present but disabled (primary moved to sellerclaw-ui): channel emitted disabled, no binding.
    m = copy.deepcopy(load_manifest_v2_mapping())
    m["channels"]["primary"] = "sellerclaw-ui"
    m["channels"]["telegram"]["enabled"] = False
    cfg_off = _cfg_from_mapping(m)
    assert cfg_off["channels"]["telegram"]["enabled"] is False
    assert all(b["match"].get("channel") != "telegram" for b in cfg_off["bindings"])


def test_supervisor_media_builtins_denied_never_allowed() -> None:
    """Media generation goes through sellerclaw-cli; the builtin tools are denied for the
    supervisor and never appear in ``allow`` (regardless of the manifest image/video flags),
    so OpenClaw does not warn about an unknown tool in the allowlist."""
    for image_video in (True, False):
        m = copy.deepcopy(load_manifest_v2_mapping())
        m["agents"]["main_agent"]["image_generation"] = image_video
        m["agents"]["main_agent"]["video_generation"] = image_video
        tools = _agent_payload(_cfg_from_mapping(m), "supervisor")["tools"]
        assert "image_generate" not in tools["allow"]
        assert "video_generate" not in tools["allow"]
        assert "image_generate" in tools["deny"]
        assert "video_generate" in tools["deny"]


def test_sellerclaw_ui_channel_has_no_dead_parts_streaming_flag() -> None:
    """parts-streaming is now unconditional in the sellerclaw-ui plugin; the config
    must not carry the obsolete ``partsStreaming`` toggle."""
    cfg = _cfg_from_mapping(copy.deepcopy(load_manifest_v2_mapping()))
    assert "partsStreaming" not in cfg["channels"]["sellerclaw-ui"]
    assert "partsStreaming" not in cfg["plugins"]["entries"]["sellerclaw-ui"]["config"]
