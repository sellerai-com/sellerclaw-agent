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


def test_assembler_supervisor_always_includes_goal_tracking_skill(agent_resources_root: Path) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert "goal-tracking" in sup.skills


def test_assembler_supervisor_reads_agents_md_over_sections_when_present(
    agent_resources_root: Path,
) -> None:
    """When ``agents/supervisor/agents.md`` exists, it becomes AGENTS.md verbatim.

    The legacy ``sections/`` bundle (core.md, goal_tracking.md, ...) is the
    fallback path — the per-agent ``agents.md`` file wins.
    """
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    expected = (agent_resources_root / "agents" / "supervisor" / "agents.md").read_text(
        encoding="utf-8"
    )
    assert sup.agents_md == expected.strip()


def test_assembler_supervisor_loads_optional_templates_from_per_agent_files(
    agent_resources_root: Path,
) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert sup.user_md is not None and "Owner dossier" in sup.user_md
    assert sup.tools_md is not None and "TOOLS.md" in sup.tools_md
    assert sup.identity_md is not None and "IDENTITY.md" in sup.identity_md
    # ``soul.md`` under the supervisor folder wins over the legacy ``souls/supervisor.md``.
    own_soul = (agent_resources_root / "agents" / "supervisor" / "soul.md").read_text(
        encoding="utf-8"
    )
    assert sup.soul_md is not None
    assert sup.soul_md == own_soul.strip() or own_soul.strip() in sup.soul_md


def test_assembler_supervisor_falls_back_to_sections_when_agents_md_missing(
    tmp_path: Path, agent_resources_root: Path
) -> None:
    """Without per-agent ``agents.md`` the assembler must rebuild AGENTS.md from ``sections/``."""
    import shutil

    mirror = tmp_path / "resources"
    shutil.copytree(agent_resources_root, mirror)
    supervisor_dir = mirror / "agents" / "supervisor"
    (supervisor_dir / "agents.md").unlink()
    sections_dir = supervisor_dir / "sections"
    sections_dir.mkdir()
    (sections_dir / "core.md").write_text(
        "# Legacy core section\nCore body text.\n", encoding="utf-8"
    )
    (sections_dir / "goal_tracking.md").write_text(
        "# Goal & Task Tracking\nLegacy goal tracking copy.\n", encoding="utf-8"
    )

    asm = AgentConfigAssembler(resources_root=mirror)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert "Legacy core section" in sup.agents_md
    assert "Goal & Task Tracking" in sup.agents_md


def test_assembler_module_loads_optional_per_agent_templates_when_present(
    tmp_path: Path, agent_resources_root: Path
) -> None:
    """Module agents also honor per-agent user/tools/identity/soul files."""
    import shutil

    shopify = get_module(AgentModuleId.SHOPIFY_STORE_MANAGER)
    assert shopify is not None

    mirror = tmp_path / "resources"
    shutil.copytree(agent_resources_root, mirror)
    shopify_dir = mirror / "agents" / "shopify"
    (shopify_dir / "user.md").write_text("# Shopify USER\n", encoding="utf-8")
    (shopify_dir / "tools.md").write_text("# Shopify TOOLS\n", encoding="utf-8")
    (shopify_dir / "identity.md").write_text("# Shopify IDENTITY\n", encoding="utf-8")
    (shopify_dir / "soul.md").write_text("# Shopify SOUL\n", encoding="utf-8")

    asm = AgentConfigAssembler(resources_root=mirror)
    mod = asm.assemble(
        enabled_modules=[shopify],
        template_variables=_template_vars(),
        connected_integrations=frozenset(),
        global_browser_enabled=True,
    )[1]
    assert mod.user_md == "# Shopify USER\n"
    assert mod.tools_md == "# Shopify TOOLS\n"
    assert mod.identity_md == "# Shopify IDENTITY\n"
    assert mod.soul_md == "# Shopify SOUL\n"


def test_assembler_module_without_optional_templates_keeps_subagent_soul_fallback(
    agent_resources_root: Path,
) -> None:
    """When no per-agent soul.md exists, the module soul still comes from ``souls/subagent.md``."""
    shopify = get_module(AgentModuleId.SHOPIFY_STORE_MANAGER)
    assert shopify is not None
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    mod = asm.assemble(
        enabled_modules=[shopify],
        template_variables=_template_vars(),
        connected_integrations=frozenset(),
        global_browser_enabled=True,
    )[1]
    subagent_soul_raw = (agent_resources_root / "souls" / "subagent.md").read_text(encoding="utf-8")
    assert mod.soul_md is not None
    assert subagent_soul_raw.splitlines()[0] in mod.soul_md
    assert mod.user_md is None
    assert mod.tools_md is None
    assert mod.identity_md is None


def test_assembler_supervisor_excludes_shared_skills_from_per_agent_dict(
    agent_resources_root: Path,
) -> None:
    """Shared skills (``agent_resources/shared-skills``) must not land in the per-agent ``skills`` dict.

    They are exposed separately via ``assemble_shared_skills`` so the runtime can
    drop them into OpenClaw's machine-wide managed-skills directory instead of
    duplicating them into every agent workspace.
    """
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    # ``file-storage`` is a shared skill referenced by ``base_supervisor_skills``.
    assert "file-storage" not in sup.skills
    assert "task-reporting" not in sup.skills


def test_assembler_shared_skills_exposes_all_shared_skills(agent_resources_root: Path) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    shared = asm.assemble_shared_skills(_template_vars())
    assert set(shared.keys()) == {"file-storage", "task-reporting"}
    assert all(content.strip() for content in shared.values())


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
