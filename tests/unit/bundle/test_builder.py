from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sellerclaw_agent.bundle.builder import BundleBuilder, derive_agent_tools
from sellerclaw_agent.bundle.manifest import GenericManifest

pytestmark = pytest.mark.unit

_GW = "gw"
_HOOKS = "hooks"


def _agent_payload(cfg: dict, agent_id: str) -> dict:
    for agent in cfg["agents"]["list"]:
        if agent["id"] == agent_id:
            return agent
    raise AssertionError(f"agent {agent_id!r} not in config")


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


def test_bundle_builder_entry_point_tools_and_subagents(
    make_manifest: Callable[..., GenericManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    result = BundleBuilder().build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    cfg = json.loads(result.openclaw_config)
    supervisor = _agent_payload(cfg, "supervisor")
    assert supervisor["default"] is True
    assert "group:sessions" in supervisor["tools"]["allow"]
    assert supervisor["subagents"]["allowAgents"] == ["scout", "supplier", "marketing"]


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
    ("is_entry_point", "has_subagents", "browser", "image", "video", "expected_allow", "expected_deny"),
    [
        pytest.param(
            True,
            True,
            True,
            True,
            True,
            [
                "group:sessions",
                "group:web",
                "web_search",
                "message",
                "browser",
                "cron",
                "exec",
                "image_generate",
                "video_generate",
                "pdf",
            ],
            [],
            id="entry-point-with-subagents",
        ),
        pytest.param(
            True,
            False,
            True,
            True,
            True,
            [
                "group:web",
                "web_search",
                "message",
                "browser",
                "cron",
                "exec",
                "image_generate",
                "video_generate",
                "pdf",
                "group:fs",
                "process",
            ],
            [],
            id="entry-point-no-subagents",
        ),
        pytest.param(
            False,
            False,
            True,
            False,
            False,
            ["group:fs", "exec", "process", "web_fetch", "web_search", "browser", "pdf"],
            ["group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"],
            id="subagent-no-media",
        ),
        pytest.param(
            False,
            False,
            True,
            True,
            True,
            [
                "group:fs",
                "exec",
                "process",
                "web_fetch",
                "web_search",
                "browser",
                "pdf",
                "image_generate",
                "video_generate",
            ],
            ["group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"],
            id="subagent-with-media",
        ),
        pytest.param(
            False,
            False,
            False,
            False,
            False,
            ["group:fs", "exec", "process", "web_fetch", "web_search", "pdf"],
            ["group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"],
            id="subagent-browser-disabled",
        ),
    ],
)
def test_derive_agent_tools(
    is_entry_point: bool,
    has_subagents: bool,
    browser: bool,
    image: bool,
    video: bool,
    expected_allow: list[str],
    expected_deny: list[str],
) -> None:
    allow, deny = derive_agent_tools(
        is_entry_point=is_entry_point,
        has_subagents=has_subagents,
        browser_enabled=browser,
        image_generation=image,
        video_generation=video,
    )
    assert allow == expected_allow
    assert deny == expected_deny
