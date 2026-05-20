from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.bundle.archive import build_gateway_version, build_workspaces_from_assembled
from sellerclaw_agent.bundle.config_generator import generate_openclaw_config
from sellerclaw_agent.bundle.manifest import AgentSpec, GenericManifest, ModelRef
from sellerclaw_agent.bundle.result import BundleResult
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token_from_data_dir
from sellerclaw_agent.cloud.settings import (
    get_admin_url,
    get_sellerclaw_api_url,
    get_sellerclaw_web_url,
)
from sellerclaw_agent.models import ModelTier


def _compose_agent_api_base_url(
    *,
    sellerclaw_api_url: str,
    agent_api_base_path: str,
) -> str:
    """Derive the agent API base URL (``SELLERCLAW_AGENT_API_BASE_URL``).

    Concatenates the deployment-level host (``SELLERCLAW_API_URL``) with the
    manifest-supplied ``agent_api_base_path`` (e.g. ``/agent``). Used for the
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


def derive_agent_tools(
    *,
    is_entry_point: bool,
    has_subagents: bool,
    browser_enabled: bool,
    image_generation: bool,
    video_generation: bool,
) -> tuple[list[str], list[str]]:
    """Derive the OpenClaw ``tools.allow`` / ``tools.deny`` lists for one agent.

    Returns ``(allow, deny)``. Mirrors the prior assembler behavior:

    - Entry point (supervisor): broad allow; ``group:sessions`` when it has subagents,
      otherwise ``group:fs`` + ``process``. No denies.
    - Subagent: filesystem/exec/process/web + browser/pdf; image/video gated by flags;
      denies the orchestration/messaging surface.
    - ``browser`` is stripped from allow when ``browser_enabled`` is False.
    """
    if is_entry_point:
        allow = [
            "group:web",
            "web_search",
            "message",
            "browser",
            "cron",
            "exec",
            "image_generate",
            "video_generate",
            "pdf",
        ]
        if has_subagents:
            allow = ["group:sessions", *allow]
        else:
            allow.extend(["group:fs", "process"])
        deny: list[str] = []
    else:
        allow = [
            "group:fs",
            "exec",
            "process",
            "web_fetch",
            "web_search",
            "browser",
            "pdf",
        ]
        if image_generation:
            allow.append("image_generate")
        if video_generation:
            allow.append("video_generate")
        deny = [
            "group:sessions",
            "group:messaging",
            "canvas",
            "nodes",
            "cron",
            "gateway",
        ]
    if not browser_enabled:
        allow = [tool for tool in allow if tool != "browser"]
    return allow, deny


def _resolve_model_tier(manifest: GenericManifest, model_role: str) -> ModelTier:
    """Resolve an agent's ``model`` role to a :class:`ModelTier`.

    ``primary``/``secondary`` index into ``llm.text_model``. A LiteLLM ``complex``
    ref maps to ``COMPLEX``; everything else maps to ``SIMPLE``.
    """
    ref: ModelRef | None = manifest.llm.text_model.get(model_role)
    if ref is None:
        ref = manifest.llm.text_model.get("primary")
    if ref is not None and ref.group == "litellm" and ref.model == "complex":
        return ModelTier.COMPLEX
    return ModelTier.SIMPLE


@dataclass
class BundleBuilder:
    """Build an OpenClaw gateway bundle from a pre-rendered :class:`GenericManifest`."""

    def build(
        self,
        manifest: GenericManifest,
        *,
        gateway_token: str,
        hooks_token: str,
        agent_api_key: str | None = None,
        data_dir: Path | None = None,
        created_at: datetime | None = None,
    ) -> BundleResult:
        # The agent API key is mandatory for every bundle: it authenticates the
        # sellerclaw-ui plugin's outbound calls and (when enabled) OpenClaw's
        # web-search tool. Fail fast with a single actionable message.
        resolved_api_key = self._resolve_agent_api_key(agent_api_key, data_dir)
        if not resolved_api_key:
            raise ValueError(
                "An agent API key is required in openclaw config (sellerclaw-ui). "
                "Sign in to SellerClaw (agent_token.json under SELLERCLAW_DATA_DIR) or set AGENT_API_KEY."
            )

        assembled = [
            self._assemble_agent(manifest, agent) for agent in manifest.all_agents()
        ]

        sellerclaw_api_url = get_sellerclaw_api_url()
        agent_api_base_url = _compose_agent_api_base_url(
            sellerclaw_api_url=sellerclaw_api_url,
            agent_api_base_path=manifest.agent_api_base_path,
        )
        allowed_origins = _resolve_allowed_origins()

        litellm_group = manifest.litellm_group()
        litellm_base_url = litellm_group.base_url if litellm_group is not None else ""
        litellm_api_key = litellm_group.api_key if litellm_group is not None else ""
        prefix = manifest.model_name_prefix
        model_name_prefix = prefix if prefix else None

        web_search_enabled = manifest.web_search.enabled
        web_search_auth_token = resolved_api_key if web_search_enabled else ""

        ts = created_at or datetime.now(tz=UTC)
        workspaces = build_workspaces_from_assembled(assembled)
        openclaw_config = generate_openclaw_config(
            assembled_agents=assembled,
            gateway_token=gateway_token,
            hooks_token=hooks_token,
            agent_api_key=resolved_api_key,
            user_id=manifest.user_id,
            sellerclaw_api_url=sellerclaw_api_url,
            sellerclaw_agent_api_base_url=agent_api_base_url,
            litellm_base_url=litellm_base_url,
            litellm_api_key=litellm_api_key,
            model_name_prefix=model_name_prefix,
            created_at=ts,
            telegram_enabled=manifest.channels.telegram.enabled,
            telegram_bot_token=manifest.channels.telegram.bot_token,
            telegram_allowed_user_ids=manifest.channels.telegram.allowed_user_ids,
            telegram_allowed_group_ids=manifest.channels.telegram.allowed_group_ids,
            allowed_origins=allowed_origins,
            browser_enabled=manifest.agents.browser_enabled_default,
            web_search_enabled=web_search_enabled,
            web_search_auth_token=web_search_auth_token,
            primary_channel=manifest.channels.primary,
        )
        version = build_gateway_version(
            openclaw_config=openclaw_config,
            workspaces=workspaces,
            shared_skills={},
        )
        return BundleResult(
            openclaw_config=openclaw_config,
            workspaces=workspaces,
            shared_skills={},
            version=version,
            created_at=ts,
        )

    @staticmethod
    def _resolve_agent_api_key(
        agent_api_key: str | None,
        data_dir: Path | None,
    ) -> str | None:
        if agent_api_key and agent_api_key.strip():
            return agent_api_key.strip()
        if data_dir is not None:
            resolved = resolve_agent_bearer_token_from_data_dir(data_dir)
            if resolved:
                return resolved
        return (os.environ.get("AGENT_API_KEY") or "").strip() or None

    def _assemble_agent(
        self,
        manifest: GenericManifest,
        agent: AgentSpec,
    ) -> AssembledAgentConfig:
        tools_allow, tools_deny = derive_agent_tools(
            is_entry_point=agent.is_entry_point,
            has_subagents=bool(agent.subagent_ids),
            browser_enabled=agent.browser_enabled,
            image_generation=agent.image_generation,
            video_generation=agent.video_generation,
        )
        content = agent.content
        # Heartbeat is honored only for the main (entry-point) agent, matching prior behavior.
        heartbeat_md = content.heartbeat if agent.is_entry_point else None
        return AssembledAgentConfig(
            agent_id=agent.id,
            name=agent.name,
            model_tier=_resolve_model_tier(manifest, agent.model),
            is_entry_point=agent.is_entry_point,
            subagent_ids=list(agent.subagent_ids),
            tools_allow=tools_allow,
            tools_deny=tools_deny,
            agents_md=content.instructions,
            memory_md=f"# Agent memory: {agent.id}\n",
            soul_md=content.soul,
            user_md=content.user_context,
            tools_md=content.tools_doc,
            identity_md=content.identity,
            heartbeat_md=heartbeat_md,
            thinking_default=agent.thinking,
            skills=content.skills_mapping(),
        )
