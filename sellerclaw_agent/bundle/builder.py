from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sellerclaw_agent.bundle.archive import build_gateway_version, build_workspaces_from_assembled
from sellerclaw_agent.bundle.assembler import AgentConfigAssembler
from sellerclaw_agent.bundle.config_generator import generate_openclaw_config
from sellerclaw_agent.bundle.manifest import BundleManifest
from sellerclaw_agent.bundle.result import BundleResult
from sellerclaw_agent.cloud.settings import (
    get_admin_url,
    get_sellerclaw_api_url,
    get_sellerclaw_web_url,
)
from sellerclaw_agent.registry import get_module


_API_BASE_PATH_KEY = "api_base_path"
_API_BASE_URL_KEY = "api_base_url"


def _resolve_template_variables(
    manifest_variables: dict[str, str],
    *,
    sellerclaw_api_url: str,
) -> dict[str, str]:
    """Expand ``api_base_path`` into the full ``api_base_url`` consumed by prompts.

    Prompt templates (``SKILL.md`` etc.) reference ``{{ api_base_url }}``; the
    manifest ships only the path part (e.g. ``/agent``) so the deployment-specific
    host from ``SELLERCLAW_API_URL`` stays out of the wire contract.
    """
    resolved = dict(manifest_variables)
    path = str(resolved.pop(_API_BASE_PATH_KEY, "")).strip()
    base = sellerclaw_api_url.strip().rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}" if path else ""
    resolved[_API_BASE_URL_KEY] = f"{base}{path}"
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
        model_name_prefix: str | None = None,
        created_at: datetime | None = None,
    ) -> BundleResult:
        enabled_definitions = []
        for mid in manifest.resolved_enabled_modules():
            definition = get_module(mid)
            if definition is None:
                raise ValueError(f"Unknown module id: {mid!r}")
            enabled_definitions.append(definition)

        sellerclaw_api_url = get_sellerclaw_api_url()
        template_variables = _resolve_template_variables(
            dict(manifest.template_variables),
            sellerclaw_api_url=sellerclaw_api_url,
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
        workspaces = build_workspaces_from_assembled(assembled)
        openclaw_config = generate_openclaw_config(
            assembled_agents=assembled,
            gateway_token=manifest.gateway_token,
            hooks_token=manifest.hooks_token,
            user_id=manifest.user_id,
            sellerclaw_api_url=sellerclaw_api_url,
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
            web_search_provider=manifest.web_search.provider,
            web_search_api_key=manifest.web_search.api_key,
            primary_channel=manifest.primary_channel,
        )
        version = build_gateway_version(openclaw_config=openclaw_config, workspaces=workspaces)
        ts = created_at or datetime.now(tz=UTC)
        return BundleResult(
            openclaw_config=openclaw_config,
            workspaces=workspaces,
            version=version,
            created_at=ts,
        )
