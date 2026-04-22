from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sellerclaw_agent.bundle.builder import BundleBuilder
from sellerclaw_agent.bundle.manifest import WebSearchManifest
from sellerclaw_agent.models import AgentModuleId
from sellerclaw_agent.registry import get_module

pytestmark = pytest.mark.unit

_GW = "gw"
_HOOKS = "hooks"


def test_bundle_builder_produces_config_and_workspaces(
    agent_resources_root: Path,
    make_manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    builder = BundleBuilder(resources_root=agent_resources_root)
    result = builder.build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    assert '"gateway"' in result.openclaw_config
    assert "supervisor/AGENTS.md" in result.workspaces
    assert len(result.version) == 64


def test_bundle_builder_invalid_module_id_raises(
    agent_resources_root: Path,
    make_manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest(enabled_module_ids=("not_a_real_module",))
    builder = BundleBuilder(resources_root=agent_resources_root)
    with pytest.raises(ValueError, match="not a valid AgentModuleId"):
        builder.build(manifest, gateway_token=_GW, hooks_token=_HOOKS)


def test_bundle_builder_with_enabled_module_includes_module_workspace(
    agent_resources_root: Path,
    make_manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    shopify = get_module(AgentModuleId.SHOPIFY_STORE_MANAGER)
    assert shopify is not None
    manifest = make_manifest(enabled_module_ids=(str(shopify.id.value),))
    builder = BundleBuilder(resources_root=agent_resources_root)
    result = builder.build(manifest, gateway_token=_GW, hooks_token=_HOOKS)
    assert "shopify/AGENTS.md" in result.workspaces
    assert "shopify/MEMORY.md" in result.workspaces


def test_bundle_builder_created_at_override(
    agent_resources_root: Path,
    make_manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-agent-key")
    manifest = make_manifest()
    fixed = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    result = BundleBuilder(resources_root=agent_resources_root).build(
        manifest,
        gateway_token=_GW,
        hooks_token=_HOOKS,
        created_at=fixed,
    )
    assert result.created_at == fixed


def test_bundle_builder_web_search_enabled_requires_bearer(
    agent_resources_root: Path,
    make_manifest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    manifest = make_manifest(web_search=WebSearchManifest(enabled=True))
    builder = BundleBuilder(resources_root=agent_resources_root)
    with pytest.raises(ValueError, match="agent API key is required"):
        builder.build(manifest, gateway_token=_GW, hooks_token=_HOOKS, data_dir=tmp_path)


def test_bundle_builder_web_search_enabled_uses_agent_api_key_env(
    agent_resources_root: Path,
    make_manifest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "sca_from_env")
    monkeypatch.setattr(
        "sellerclaw_agent.bundle.builder.get_sellerclaw_api_url",
        lambda: "https://sellerclaw.example",
    )
    manifest = make_manifest(web_search=WebSearchManifest(enabled=True))
    builder = BundleBuilder(resources_root=agent_resources_root)
    result = builder.build(manifest, gateway_token=_GW, hooks_token=_HOOKS, data_dir=tmp_path)
    cfg = json.loads(result.openclaw_config)
    web_search_cfg = cfg["plugins"]["entries"]["sellerclaw-web-search"]["config"]["webSearch"]
    assert web_search_cfg["authToken"] == "sca_from_env"
    # Derived SELLERCLAW_AGENT_API_BASE_URL = SELLERCLAW_API_URL + agent_api_base_path.
    # The plugin baseUrl must already include the agent prefix so its ``/research/web-search``
    # call resolves to ``/agent/research/web-search`` on the monolith.
    assert web_search_cfg["baseUrl"] == "https://sellerclaw.example/agent"


def test_bundle_builder_web_search_baseurl_respects_manifest_agent_api_base_path(
    agent_resources_root: Path,
    make_manifest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The web-search plugin baseUrl must follow the manifest-supplied path segment."""
    monkeypatch.setenv("AGENT_API_KEY", "sca_from_env")
    monkeypatch.setattr(
        "sellerclaw_agent.bundle.builder.get_sellerclaw_api_url",
        lambda: "https://api.example.com",
    )
    manifest = make_manifest(
        web_search=WebSearchManifest(enabled=True),
        agent_api_base_path="/custom/agent",
    )
    builder = BundleBuilder(resources_root=agent_resources_root)
    result = builder.build(manifest, gateway_token=_GW, hooks_token=_HOOKS, data_dir=tmp_path)
    cfg = json.loads(result.openclaw_config)
    web_search_cfg = cfg["plugins"]["entries"]["sellerclaw-web-search"]["config"]["webSearch"]
    assert web_search_cfg["baseUrl"] == "https://api.example.com/custom/agent"


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


def test_resolve_template_variables_injects_api_base_url() -> None:
    """`api_base_url` must come from the derived value, never from manifest input."""
    from sellerclaw_agent.bundle.builder import _resolve_template_variables

    resolved = _resolve_template_variables(
        {"user_name": "Ada", "api_base_url": "should-be-overwritten"},
        agent_api_base_url="https://api.example.com/agent",
    )
    assert resolved["api_base_url"] == "https://api.example.com/agent"
    assert resolved["user_name"] == "Ada"


def test_bundle_builder_web_search_enabled_requires_sellerclaw_api_url(
    agent_resources_root: Path,
    make_manifest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "sca_from_env")
    monkeypatch.setattr("sellerclaw_agent.bundle.builder.get_sellerclaw_api_url", lambda: "   ")
    manifest = make_manifest(web_search=WebSearchManifest(enabled=True))
    builder = BundleBuilder(resources_root=agent_resources_root)
    with pytest.raises(ValueError, match="SELLERCLAW_API_URL"):
        builder.build(manifest, gateway_token=_GW, hooks_token=_HOOKS, data_dir=tmp_path)
