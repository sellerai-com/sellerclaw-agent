from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sellerclaw_agent.bundle.archive import build_gateway_version, build_workspaces_from_assembled
from sellerclaw_agent.bundle.assembler import AgentConfigAssembler
from sellerclaw_agent.bundle.config_generator import generate_openclaw_config
from sellerclaw_agent.bundle.manifest import BundleManifest
from sellerclaw_agent.bundle.result import BundleResult
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token_from_data_dir
from sellerclaw_agent.cloud.settings import (
    get_admin_url,
    get_sellerclaw_api_url,
    get_sellerclaw_web_url,
)
from sellerclaw_agent.registry import get_module


_API_BASE_URL_KEY = "api_base_url"


def _compose_agent_api_base_url(
    *,
    sellerclaw_api_url: str,
    agent_api_base_path: str,
) -> str:
    """Derive the agent API base URL (``SELLERCLAW_AGENT_API_BASE_URL``).

    Concatenates the deployment-level host (``SELLERCLAW_API_URL``) with the
    manifest-supplied ``agent_api_base_path`` (e.g. ``/agent``). Used both for
    the ``{{ api_base_url }}`` prompt template variable and for the
    ``sellerclaw-web-search`` plugin ``baseUrl``.

    Returns an empty string when the deployment host is unset so downstream
    consumers can treat "no base URL" as a single sentinel.
    """
    base = sellerclaw_api_url.strip().rstrip("/")
    if not base:
        return ""
    path = (agent_api_base_path or "").strip().rstrip("/")
    if path and not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _resolve_template_variables(
    manifest_variables: dict[str, str],
    *,
    agent_api_base_url: str,
) -> dict[str, str]:
    """Inject the derived ``api_base_url`` into prompt template variables.

    Prompt templates (``SKILL.md`` etc.) reference ``{{ api_base_url }}``; the
    manifest never ships it directly — the agent always derives it from
    ``SELLERCLAW_API_URL`` plus the manifest-supplied ``agent_api_base_path``.
    """
    resolved = dict(manifest_variables)
    resolved[_API_BASE_URL_KEY] = agent_api_base_url
    return resolved


def _resolve_allowed_origins() -> tuple[str, ...]:
    candidates = (get_sellerclaw_web_url(), get_admin_url())
    seen: set[str] = set()
    unique: list[str] = []
    for raw in candidates:
        value = (raw or "").strip().rstrip("/")
        if not value or value in seen:
            continue
        unique.append(value)
        seen.add(value)
    return tuple(unique)


@dataclass
class BundleBuilder:
    """Build OpenClaw gateway bundle from a flat manifest and on-disk agent_resources."""

    resources_root: Path

    def build(
        self,
        manifest: BundleManifest,
        *,
        gateway_token: str,
        hooks_token: str,
        model_name_prefix: str | None = None,
        created_at: datetime | None = None,
        data_dir: Path | None = None,
    ) -> BundleResult:
        enabled_definitions = []
        for mid in manifest.resolved_enabled_modules():
            definition = get_module(mid)
            if definition is None:
                raise ValueError(f"Unknown module id: {mid!r}")
            enabled_definitions.append(definition)

        sellerclaw_api_url = get_sellerclaw_api_url()
        agent_api_base_url = _compose_agent_api_base_url(
            sellerclaw_api_url=sellerclaw_api_url,
            agent_api_base_path=manifest.agent_api_base_path,
        )
        template_variables = _resolve_template_variables(
            dict(manifest.template_variables),
            agent_api_base_url=agent_api_base_url,
        )
        allowed_origins = _resolve_allowed_origins()

        assembler = AgentConfigAssembler(resources_root=self.resources_root)
        assembled = assembler.assemble(
            enabled_modules=enabled_definitions,
            template_variables=template_variables,
            connected_integrations=manifest.connected_integrations,
            global_browser_enabled=manifest.global_browser_enabled,
            per_module_browser=manifest.resolved_per_module_browser(),
        )
        shared_skills = assembler.assemble_shared_skills(template_variables)
        workspaces = build_workspaces_from_assembled(assembled)
        if data_dir is not None:
            agent_api_key = resolve_agent_bearer_token_from_data_dir(data_dir)
        else:
            agent_api_key = (os.environ.get("AGENT_API_KEY") or "").strip() or None
        # The agent API key is now mandatory for every bundle: it authenticates the
        # sellerclaw-ui plugin's outbound calls to the cloud and (when enabled)
        # OpenClaw's web-search tool. Fail fast with a single actionable message.
        if not agent_api_key:
            raise ValueError(
                "An agent API key is required in openclaw config (sellerclaw-ui). "
                "Sign in to SellerClaw (agent_token.json under SELLERCLAW_DATA_DIR) or set AGENT_API_KEY."
            )
        web_search_auth_token = agent_api_key if manifest.web_search.enabled else ""
        openclaw_config = generate_openclaw_config(
            assembled_agents=assembled,
            gateway_token=gateway_token,
            hooks_token=hooks_token,
            agent_api_key=agent_api_key,
            user_id=manifest.user_id,
            sellerclaw_api_url=sellerclaw_api_url,
            sellerclaw_agent_api_base_url=agent_api_base_url,
            litellm_base_url=manifest.litellm_base_url,
            litellm_api_key=manifest.litellm_api_key,
            model_complex=manifest.model_complex,
            model_simple=manifest.model_simple,
            model_name_prefix=model_name_prefix,
            telegram_enabled=manifest.telegram.enabled,
            telegram_bot_token=manifest.telegram.bot_token,
            telegram_allowed_user_ids=manifest.telegram.allowed_user_ids,
            telegram_allowed_group_ids=manifest.telegram.allowed_group_ids,
            allowed_origins=allowed_origins,
            browser_enabled=manifest.global_browser_enabled,
            web_search_enabled=manifest.web_search.enabled,
            web_search_auth_token=web_search_auth_token,
            primary_channel=manifest.primary_channel,
        )
        version = build_gateway_version(
            openclaw_config=openclaw_config,
            workspaces=workspaces,
            shared_skills=shared_skills,
        )
        ts = created_at or datetime.now(tz=UTC)
        return BundleResult(
            openclaw_config=openclaw_config,
            workspaces=workspaces,
            shared_skills=shared_skills,
            version=version,
            created_at=ts,
        )
