"""Pytest fixtures for BundleManifest (plugin: ``pytest_plugins`` in repo ``sellerclaw-agent/conftest.py``)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.bundle.manifest import (
    BundleManifest,
    ModelSpec,
    TelegramManifest,
    WebSearchManifest,
)
from sellerclaw_agent.models import IntegrationKind, ModelTier

_DEFAULT_TEMPLATE_VARIABLES: dict[str, str] = {
    "user_name": "Test",
    "config_generated_at": "2026-01-01 00:00 UTC",
    "available_supplier_providers": "",
    "stores_list": "_No stores connected._\n",
    "suppliers_list": "_No supplier accounts connected._\n",
    "subagents_list": "none",
    "ad_strategy_settings": "No strategy settings configured -- use defaults.\n",
    "telegram_group_id": "",
}

_DEFAULT_AGENT_API_BASE_PATH = "/agent"


@pytest.fixture
def agent_resources_root() -> Path:
    # sellerclaw_agent/test_manifest_fixtures.py -> parent.parent = sellerclaw-agent repo root
    return Path(__file__).resolve().parent.parent / "agent_resources"


@pytest.fixture
def make_model_spec() -> Callable[..., ModelSpec]:
    def _build(
        *,
        id: str = "gpt-5.4",
        name: str = "GPT-5.4",
        reasoning: bool = True,
        input: tuple[str, ...] = ("text", "image"),
        context_window: int = 200000,
        max_tokens: int = 8192,
    ) -> ModelSpec:
        return ModelSpec(
            id=id,
            name=name,
            reasoning=reasoning,
            input=input,
            context_window=context_window,
            max_tokens=max_tokens,
        )

    return _build


@pytest.fixture
def make_manifest(
    make_model_spec: Callable[..., ModelSpec],
) -> Callable[..., BundleManifest]:
    def _build(
        *,
        user_id: UUID | None = None,
        gateway_token: str = "gw",
        hooks_token: str = "hooks",
        litellm_base_url: str = "http://litellm:4000",
        litellm_api_key: str = "key",
        model_complex: ModelSpec | None = None,
        model_simple: ModelSpec | None = None,
        template_variables: dict[str, str] | None = None,
        enabled_module_ids: tuple[str, ...] = (),
        connected_integrations: frozenset[IntegrationKind] | None = None,
        global_browser_enabled: bool = True,
        per_module_browser: dict[str, bool] | None = None,
        telegram: TelegramManifest | None = None,
        web_search: WebSearchManifest | None = None,
        primary_channel: str = "sellerclaw-ui",
        proxy_url: str = "",
        model_name_prefix: str = "",
        agent_api_base_path: str = _DEFAULT_AGENT_API_BASE_PATH,
    ) -> BundleManifest:
        mc = model_complex or make_model_spec()
        ms = model_simple or make_model_spec(
            id="gpt-5.4-mini",
            name="GPT-5.4 Mini",
            reasoning=False,
            context_window=128000,
            max_tokens=4096,
        )
        tv = dict(_DEFAULT_TEMPLATE_VARIABLES)
        if template_variables is not None:
            tv.update(template_variables)
        pmb = per_module_browser if per_module_browser is not None else {}
        conn = connected_integrations if connected_integrations is not None else frozenset()
        return BundleManifest(
            user_id=user_id or UUID("11111111-1111-4111-8111-111111111111"),
            gateway_token=gateway_token,
            hooks_token=hooks_token,
            litellm_base_url=litellm_base_url,
            litellm_api_key=litellm_api_key,
            model_complex=mc,
            model_simple=ms,
            template_variables=tv,
            enabled_module_ids=enabled_module_ids,
            connected_integrations=conn,
            global_browser_enabled=global_browser_enabled,
            per_module_browser=pmb,
            telegram=telegram if telegram is not None else TelegramManifest(),
            web_search=web_search if web_search is not None else WebSearchManifest(),
            primary_channel=primary_channel,
            proxy_url=proxy_url,
            model_name_prefix=model_name_prefix,
            agent_api_base_path=agent_api_base_path,
        )

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
            skills=skills if skills is not None else {"file-storage": "# File Storage"},
        )

    return _make
