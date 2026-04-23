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
    }


def test_assembler_supervisor_only_renders_template_vars(agent_resources_root: Path) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert sup.agent_id == "supervisor"
    assert "http://x/agent" in sup.agents_md or len(sup.agents_md) > 10


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


def test_assembler_supervisor_always_includes_goal_section_and_skill(agent_resources_root: Path) -> None:
    asm = AgentConfigAssembler(resources_root=agent_resources_root)
    sup = asm.assemble_supervisor_only(template_variables=_template_vars())
    assert "Goal & Task Tracking" in sup.agents_md
    assert "goal-tracking" in sup.skills


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
