from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sellerclaw_agent.bundle.builder import BundleBuilder
from sellerclaw_agent.models import AgentModuleId
from sellerclaw_agent.registry import get_module

pytestmark = pytest.mark.unit


def test_bundle_builder_produces_config_and_workspaces(
    agent_resources_root: Path,
    make_manifest,
) -> None:
    manifest = make_manifest()
    builder = BundleBuilder(resources_root=agent_resources_root)
    result = builder.build(manifest)
    assert '"gateway"' in result.openclaw_config
    assert "supervisor/AGENTS.md" in result.workspaces
    assert len(result.version) == 64


def test_bundle_builder_invalid_module_id_raises(agent_resources_root: Path, make_manifest) -> None:
    manifest = make_manifest(enabled_module_ids=("not_a_real_module",))
    builder = BundleBuilder(resources_root=agent_resources_root)
    with pytest.raises(ValueError, match="not a valid AgentModuleId"):
        builder.build(manifest)


def test_bundle_builder_with_enabled_module_includes_module_workspace(
    agent_resources_root: Path,
    make_manifest,
) -> None:
    shopify = get_module(AgentModuleId.SHOPIFY_STORE_MANAGER)
    assert shopify is not None
    manifest = make_manifest(enabled_module_ids=(str(shopify.id.value),))
    builder = BundleBuilder(resources_root=agent_resources_root)
    result = builder.build(manifest)
    assert "shopify/AGENTS.md" in result.workspaces
    assert "shopify/MEMORY.md" in result.workspaces


def test_bundle_builder_created_at_override(agent_resources_root: Path, make_manifest) -> None:
    manifest = make_manifest()
    fixed = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    result = BundleBuilder(resources_root=agent_resources_root).build(manifest, created_at=fixed)
    assert result.created_at == fixed
