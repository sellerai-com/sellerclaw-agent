"""Pytest fixtures for the generic manifest (``pytest_plugins`` in repo ``conftest.py``)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.bundle.manifest import GenericManifest, bundle_manifest_from_mapping
from sellerclaw_agent.models import ModelTier

_MANIFEST_V2_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "unit" / "bundle" / "data" / "manifest_v2.json"
)


def load_manifest_v2_mapping() -> dict[str, Any]:
    """Load the frozen v2 manifest contract fixture as a plain mapping."""
    raw = json.loads(_MANIFEST_V2_PATH.read_text(encoding="utf-8"))
    return {str(k): v for k, v in raw.items()}


@pytest.fixture
def make_manifest() -> Callable[..., GenericManifest]:
    """Factory returning a parsed :class:`GenericManifest` from the v2 fixture.

    Supports a few common keyword overrides applied to the mapping before parsing:
    ``web_search_enabled``, ``agent_api_base_path``, ``proxy_url`` and a generic
    ``overrides`` dict merged shallowly at the top level.
    """

    def _build(
        *,
        web_search_enabled: bool | None = None,
        agent_api_base_path: str | None = None,
        proxy_url: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> GenericManifest:
        mapping = copy.deepcopy(load_manifest_v2_mapping())
        if web_search_enabled is not None:
            mapping["web_search"] = {"enabled": web_search_enabled}
        if agent_api_base_path is not None:
            mapping["agent_api_base_path"] = agent_api_base_path
        if proxy_url is not None:
            mapping["proxy_url"] = proxy_url
        if overrides:
            mapping.update(overrides)
        return bundle_manifest_from_mapping(mapping)

    return _build


@pytest.fixture
def make_assembled_agent() -> Callable[..., AssembledAgentConfig]:
    def _make(
        *,
        agent_id: str = "supervisor",
        name: str = "Supervisor",
        model_tier: ModelTier = ModelTier.COMPLEX,
        is_entry_point: bool = True,
        subagent_ids: list[str] | None = None,
        tools_allow: list[str] | None = None,
        tools_deny: list[str] | None = None,
        agents_md: str = "# OpenClaw Agent: supervisor",
        memory_md: str = "# Agent memory: supervisor\n",
        soul_md: str | None = "# SOUL.md\n",
        user_md: str | None = "# USER.md\n",
        tools_md: str | None = "# TOOLS.md\n",
        identity_md: str | None = "# IDENTITY.md\n",
        heartbeat_md: str | None = None,
        thinking_default: str | None = None,
        skills: dict[str, str] | None = None,
    ) -> AssembledAgentConfig:
        return AssembledAgentConfig(
            agent_id=agent_id,
            name=name,
            model_tier=model_tier,
            is_entry_point=is_entry_point,
            subagent_ids=subagent_ids if subagent_ids is not None else [],
            tools_allow=tools_allow if tools_allow is not None else ["exec"],
            tools_deny=tools_deny if tools_deny is not None else [],
            agents_md=agents_md,
            memory_md=memory_md,
            soul_md=soul_md,
            user_md=user_md,
            tools_md=tools_md,
            identity_md=identity_md,
            heartbeat_md=heartbeat_md,
            thinking_default=thinking_default,
            skills=skills if skills is not None else {"file-storage": "# File Storage"},
        )

    return _make
