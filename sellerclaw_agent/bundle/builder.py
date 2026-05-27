from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.bundle.archive import build_gateway_version, build_workspaces_from_assembled
from sellerclaw_agent.bundle.config_generator import ModelDefaults, generate_openclaw_config
from sellerclaw_agent.bundle.manifest import AgentSpec, GenericManifest, ModelGroup, ModelRef
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
    cron_enabled: bool,
    whatsapp_enabled: bool = False,
) -> tuple[list[str], list[str]]:
    """Derive the OpenClaw ``tools.allow`` / ``tools.deny`` lists for one agent.

    Returns ``(allow, deny)``. All capability tools are manifest-driven:

    - Entry point (supervisor): broad allow; ``group:sessions`` when it has subagents,
      otherwise ``group:fs`` + ``process``. ``cron`` only here, and only when enabled.
      ``whatsapp_login`` (in-chat QR pairing tool) only here, and only when WhatsApp is on.
    - Subagent: filesystem/exec/process/web + browser/pdf; ``cron`` is always denied.
    - ``browser`` is present per agent only when ``browser_enabled``. The builtin
      ``image_generate`` / ``video_generate`` tools are **never** allowed here: media generation
      goes through sellerclaw-cli (cloud endpoints) and the builtins are denied in config
      generation, so adding them to ``allow`` only produces an "unknown tool" warning.
    """
    if is_entry_point:
        allow = ["group:web", "web_search", "message", "browser", "exec", "pdf"]
        if cron_enabled:
            allow.append("cron")
        # WhatsApp links a personal account by QR; the agent runs ``whatsapp_login`` in the
        # chat to render the QR image for the seller to scan. Only granted when the channel
        # is enabled so the tool surface stays minimal otherwise.
        if whatsapp_enabled:
            allow.append("whatsapp_login")
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


def _openclaw_model_ref(manifest: GenericManifest, ref: ModelRef) -> str:
    """Render a manifest model ref as an OpenClaw provider ref: ``group/<prefix><model>``.

    The group's ``model_name_prefix`` is applied (non-empty only for the LiteLLM
    virtual group), matching how the provider models are registered in the config.
    """
    group = manifest.llm.groups.get(ref.group)
    prefix = group.model_name_prefix if group is not None else ""
    return f"{ref.group}/{prefix}{ref.model}"


# LiteLLM virtual media group role ids. Together with the manifest's image/video model refs
# (see ``_media_model_refs``), these are NOT rendered into OpenClaw ``models.providers``: media
# generation goes through sellerclaw-cli (cloud endpoints), the builtin image_generate /
# video_generate tools are denied, and the underlying media models (e.g. Veo) are unused by
# OpenClaw. The ``{user}image`` / ``{user}video`` groups stay registered in LiteLLM (cloud-side)
# for the media endpoints (under the user's virtual key).
_MEDIA_VIRTUAL_GROUP_IDS = frozenset({"image", "video"})


def _media_model_refs(llm: object) -> set[tuple[str, str]]:
    """``(provider, model_id)`` pairs the manifest designates as image/video models.

    Catches both the LiteLLM virtual ``image``/``video`` groups and the underlying media
    models (e.g. ``google/veo-...``), so neither is rendered into the config.
    """
    refs: set[tuple[str, str]] = set()
    candidates: list[ModelRef | None] = [
        getattr(llm, "image_model_primary", None),
        getattr(llm, "video_model_primary", None),
        *getattr(llm, "image_model_fallbacks", ()),
        *getattr(llm, "video_model_fallbacks", ()),
    ]
    for ref in candidates:
        if ref is not None:
            refs.add((ref.group, ref.model))
    return refs


def _build_provider_models(
    provider_name: str,
    group: ModelGroup,
    media_refs: set[tuple[str, str]],
) -> list[dict[str, object]]:
    """Render a group's OpenClaw ``models[]`` list from its manifest metadata.

    The group's ``model_name_prefix`` is applied to each model id (non-empty only for the
    LiteLLM virtual group); all other metadata is copied verbatim. Image/video models are
    skipped (``media_refs`` + the LiteLLM virtual media groups).
    """
    models: list[dict[str, object]] = []
    for m in group.models:
        if (provider_name, m.id) in media_refs:
            continue
        if group.model_name_prefix and m.id in _MEDIA_VIRTUAL_GROUP_IDS:
            continue
        entry: dict[str, object] = {
            "id": f"{group.model_name_prefix}{m.id}",
            "name": m.name,
            "input": list(m.input),
        }
        # Optional sizing/reasoning keys flow through only when the manifest set them
        # (frontier model only); otherwise OpenClaw applies its own defaults.
        if m.reasoning is not None:
            entry["reasoning"] = m.reasoning
        if m.context_window is not None:
            entry["contextWindow"] = m.context_window
        if m.max_tokens is not None:
            entry["maxTokens"] = m.max_tokens
        models.append(entry)
    return models


def build_providers(manifest: GenericManifest) -> dict[str, object]:
    """Build the OpenClaw ``models.providers`` mapping from ``manifest.llm.groups``.

    Each provider is built strictly from its own group: ``baseUrl`` / ``apiKey`` come from
    the group, ``api`` is emitted only when the group declares one, and the ``models[]`` list
    carries the group's (prefixed) model ids + metadata — minus image/video models.
    """
    media_refs = _media_model_refs(manifest.llm)
    providers: dict[str, object] = {}
    for name, group in manifest.llm.groups.items():
        entry: dict[str, object] = {
            "baseUrl": group.base_url,
            "apiKey": group.api_key,
        }
        if group.api is not None:
            entry["api"] = group.api
        entry["models"] = _build_provider_models(name, group, media_refs)
        providers[name] = entry
    return providers


def _model_block(
    manifest: GenericManifest, primary: ModelRef, fallbacks: tuple[ModelRef, ...]
) -> dict[str, object]:
    block: dict[str, object] = {"primary": _openclaw_model_ref(manifest, primary)}
    if fallbacks:
        block["fallbacks"] = [_openclaw_model_ref(manifest, ref) for ref in fallbacks]
    return block


def build_model_defaults(manifest: GenericManifest) -> ModelDefaults:
    """Map the manifest ``llm`` block onto OpenClaw ``agents.defaults`` model blocks.

    - ``model`` <- ``text_model.primary`` (always present).
    - ``imageGenerationModel`` / ``videoGenerationModel`` / ``pdfModel`` <- the matching
      ``llm.*_model`` block, or ``None`` (key omitted) when the manifest lacks it.
    - ``compaction`` / ``memoryFlush`` <- ``compaction_model`` / ``memory_flush_model``,
      falling back to ``text_model.primary`` when the manifest omits them.
    """
    llm = manifest.llm
    text_primary_ref = _openclaw_model_ref(manifest, llm.text_model["primary"])
    return ModelDefaults(
        model={"primary": text_primary_ref},
        compaction_model=(
            _openclaw_model_ref(manifest, llm.compaction_model)
            if llm.compaction_model is not None
            else text_primary_ref
        ),
        memory_flush_model=(
            _openclaw_model_ref(manifest, llm.memory_flush_model)
            if llm.memory_flush_model is not None
            else text_primary_ref
        ),
        image_generation_model=(
            _model_block(manifest, llm.image_model_primary, llm.image_model_fallbacks)
            if llm.image_model_primary is not None
            else None
        ),
        video_generation_model=(
            _model_block(manifest, llm.video_model_primary, llm.video_model_fallbacks)
            if llm.video_model_primary is not None
            else None
        ),
        pdf_model=(
            _model_block(manifest, llm.pdf_model_primary, llm.pdf_model_fallbacks)
            if llm.pdf_model_primary is not None
            else None
        ),
    )


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
            providers=build_providers(manifest),
            created_at=ts,
            telegram_enabled=manifest.channels.telegram.enabled,
            telegram_bot_token=manifest.channels.telegram.bot_token,
            telegram_allowed_user_ids=manifest.channels.telegram.allowed_user_ids,
            telegram_allowed_group_ids=manifest.channels.telegram.allowed_group_ids,
            whatsapp_enabled=manifest.channels.whatsapp.enabled,
            whatsapp_allowed_numbers=manifest.channels.whatsapp.allowed_numbers,
            allowed_origins=allowed_origins,
            browser_enabled=manifest.agents.browser_enabled_default,
            web_search_enabled=web_search_enabled,
            web_search_auth_token=web_search_auth_token,
            primary_channel=manifest.channels.primary,
            model_defaults=build_model_defaults(manifest),
            thinking_default=manifest.agents.thinking_default,
            reasoning_default=manifest.agents.reasoning_default,
            cron_enabled=manifest.cron_enabled,
            web_fetch_enabled=manifest.web_fetch_enabled,
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
            cron_enabled=manifest.cron_enabled,
            whatsapp_enabled=manifest.channels.whatsapp.enabled,
        )
        content = agent.content
        # Heartbeat is honored only for the main (entry-point) agent, matching prior behavior.
        heartbeat_md = content.heartbeat if agent.is_entry_point else None
        # The per-agent OpenClaw model ref is the manifest text-model ref for the agent's
        # resolved role ("primary"/"secondary"), with the group prefix applied.
        model_ref = _openclaw_model_ref(manifest, manifest.llm.text_model[agent.model])
        return AssembledAgentConfig(
            agent_id=agent.id,
            name=agent.name,
            model_tier=_resolve_model_tier(manifest, agent.model),
            model_ref=model_ref,
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
