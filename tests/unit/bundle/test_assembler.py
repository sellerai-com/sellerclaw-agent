from __future__ import annotations

from pathlib import Path

import pytest
from sellerclaw_agent.bundle.assembler import AgentConfigAssembler
from sellerclaw_agent.models import AgentModuleId, IntegrationKind
from sellerclaw_agent.registry import get_module

pytestmark = pytest.mark.unit


def _template_vars() -> dict[str, str]:
    return {
        "api_base_url": "http://x/agent",
        "user_name": "U",
        "config_generated_at": "now",
        "available_supplier_providers": "",
        "stores_list": "",
        "suppliers_list": "",
        "subagents_list": "",
        "ad_strategy_settings": "",
        "telegram_group_id": "",
        "global_browser_enabled": "enabled",
        "web_search_enabled": "disabled",
        "primary_channel": "sellerclaw-ui",
        "telegram_enabled": "disabled",
        "proxy_configured": "no",
        "tools_browser_media_root": "/home/node/.openclaw/media",
        "tools_temp_exports_root": "/tmp",
        "tools_quirks": "",
    }


def test_assembler_supervisor_only_renders_template_vars(agent_resources_root: Path) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert sup.agent_id == "supervisor"
    assert "http://x/agent" in sup.agents_md or len(sup.agents_md) > 10


def test_assembler_supervisor_tools_md_expands_template_variables(agent_resources_root: Path) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert sup.tools_md is not None
    assert "http://x/agent" in sup.tools_md
    assert "{{" not in sup.tools_md


def test_assembler_with_shopify_module_subagents_and_workspace(agent_resources_root: Path) -> None:
    shopify = get_module(AgentModuleId.SHOPIFY_STORE_MANAGER)
    assert shopify is not None
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    assembled = asm.assemble(
        enabled_modules=[shopify],
        template_variables=_template_vars(),
        connected_integrations=frozenset(),
        global_browser_enabled=True,
    )
    assert len(assembled) == 2
    sup, mod = assembled[0], assembled[1]
    assert sup.subagent_ids == ["shopify"]
    assert mod.agent_id == "shopify"
    assert mod.name == "Shopify Store Manager"
    assert "shopify" in mod.agents_md.lower() or len(mod.agents_md) > 50


def test_assembler_supervisor_omits_browser_when_global_browser_disabled(agent_resources_root: Path) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(
        template_variables=_template_vars(),
        global_browser_enabled=False,
    )
    assert "browser" not in sup.tools_allow


def test_assembler_supervisor_agents_md_comes_from_agents_template(
    agent_resources_root: Path,
) -> None:
    """Bundle ``AGENTS.md`` is rendered from ``agents/supervisor/agents.md`` (partials + variables)."""
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    raw = (agent_resources_root / "agents" / "supervisor" / "agents.md").read_text(encoding="utf-8")
    assert "{{" not in sup.agents_md
    assert "SellerClaw" in sup.agents_md
    assert "{{" in raw or len(raw) > 0


def test_assembler_supervisor_loads_optional_templates_from_per_agent_files(
    agent_resources_root: Path,
) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert sup.user_md is not None and "Business profile" in sup.user_md
    assert sup.tools_md is not None and "TOOLS.md" in sup.tools_md
    assert sup.identity_md is not None and "IDENTITY.md" in sup.identity_md
    # ``soul.md`` for the supervisor lives only under ``agents/supervisor/soul.md``.
    own_soul = (agent_resources_root / "agents" / "supervisor" / "soul.md").read_text(
        encoding="utf-8"
    )
    assert sup.soul_md is not None
    assert sup.soul_md == own_soul.strip() or own_soul.strip() in sup.soul_md


def test_assembler_raises_when_supervisor_agents_md_missing(
    tmp_path: Path, agent_resources_root: Path
) -> None:
    """If ``agents/<id>/agents.md`` is absent, assembly fails before any section fallback."""
    import shutil

    mirror = tmp_path / "resources"
    shutil.copytree(agent_resources_root, mirror)
    (mirror / "agents" / "supervisor" / "agents.md").unlink()

    asm = AgentConfigAssembler(resources_root=mirror)
    with pytest.raises(FileNotFoundError, match=r"agents/supervisor/agents\.md"):
        asm.assemble_supervisor_only(template_variables=_template_vars())


def test_assembler_module_loads_optional_per_agent_templates_when_present(
    tmp_path: Path, agent_resources_root: Path
) -> None:
    """Module agents honor per-agent soul/user/tools/identity the same way as the supervisor."""
    import shutil

    shopify = get_module(AgentModuleId.SHOPIFY_STORE_MANAGER)
    assert shopify is not None

    mirror = tmp_path / "resources"
    shutil.copytree(agent_resources_root, mirror)
    shopify_dir = mirror / "agents" / "shopify"
    (shopify_dir / "soul.md").write_text("# Shopify SOUL\n", encoding="utf-8")
    (shopify_dir / "user.md").write_text("# Shopify USER\n", encoding="utf-8")
    (shopify_dir / "tools.md").write_text("# Shopify TOOLS\n", encoding="utf-8")
    (shopify_dir / "identity.md").write_text("# Shopify IDENTITY\n", encoding="utf-8")

    asm = AgentConfigAssembler(resources_root=mirror)
    mod = asm.assemble(
        enabled_modules=[shopify],
        template_variables=_template_vars(),
        connected_integrations=frozenset(),
        global_browser_enabled=True,
    )[1]
    assert mod.soul_md == "# Shopify SOUL\n"
    assert mod.user_md == "# Shopify USER\n"
    assert mod.tools_md == "# Shopify TOOLS\n"
    assert mod.identity_md == "# Shopify IDENTITY\n"


def test_assembler_module_has_no_soul_subagent_rules_in_agents_md(
    agent_resources_root: Path,
) -> None:
    """Without ``agents/<id>/soul.md``, subagents have no SOUL; executor rules live in ``AGENTS.md``."""
    shopify = get_module(AgentModuleId.SHOPIFY_STORE_MANAGER)
    assert shopify is not None
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    mod = asm.assemble(
        enabled_modules=[shopify],
        template_variables=_template_vars(),
        connected_integrations=frozenset(),
        global_browser_enabled=True,
    )[1]
    assert mod.soul_md is None
    assert "Subagent execution (shared)" in mod.agents_md
    assert mod.user_md is None
    assert mod.tools_md is None
    assert mod.identity_md is None


def test_assembler_supervisor_includes_shared_skills_in_workspace_dict(
    agent_resources_root: Path,
) -> None:
    """Shared skills are merged into the supervisor ``skills`` dict for the workspace tar."""
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert "file-storage" in sup.skills
    assert "tasks" in sup.skills


def test_assembler_shared_skills_attachment_is_empty(agent_resources_root: Path) -> None:
    """Machine-wide shared-skills copy is elided; content lives in each workspace."""
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    assert asm.assemble_shared_skills(_template_vars()) == {}


def test_assembler_scout_conditional_skills_follow_connected_integrations(agent_resources_root: Path) -> None:
    scout = get_module(AgentModuleId.PRODUCT_SCOUT)
    assert scout is not None
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    without = asm.assemble(
        enabled_modules=[scout],
        template_variables=_template_vars(),
        connected_integrations=frozenset(),
        global_browser_enabled=True,
    )[1]
    with_social = asm.assemble(
        enabled_modules=[scout],
        template_variables=_template_vars(),
        connected_integrations=frozenset({IntegrationKind.RESEARCH_SOCIAL}),
        global_browser_enabled=True,
    )[1]
    assert "social-trend-discovery" not in without.skills
    assert "social-trend-discovery" in with_social.skills
    assert "tiktok-shop-research" not in without.skills
    assert "tiktok-shop-research" in with_social.skills
