from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.bundle.archive import build_gateway_version, build_workspaces_from_assembled
from sellerclaw_agent.bundle.config_generator import ModelDefaults, generate_openclaw_config
from sellerclaw_agent.bundle.manifest import AgentSpec, GenericManifest, ModelGroup, ModelRef
from sellerclaw_agent.bundle.result import BundleResult
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token_from_data_dir
from sellerclaw_agent.cloud.settings import (
    get_openclaw_version,
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
    """CORS origins for the OpenClaw gateway UI: the SellerClaw web app, when configured."""
    value = (get_sellerclaw_web_url() or "").strip().rstrip("/")
    return (value,) if value else ()


# Long-term memory tools contributed by the openclaw-mem0 plugin (platform mode → the cloud
# Mem0-compatible adapter). Granted to EVERY agent: the plugin injects the triage protocol
# ("after responding, persist durable facts via memory_add") and rewrites recall queries, but the
# read/write tools themselves are still gated by ``tools.allow`` — without them the agent is told
# to call ``memory_add`` yet has no such tool, so it acknowledges ("Saved!") without persisting.
# Recall injection is a plugin hook and works regardless of this list; only the explicit tool calls
# need the grant. Plugin tools are not part of a builtin ``group:`` so they are listed by name.
_MEMORY_TOOLS = [
    "memory_search",
    "memory_add",
    "memory_get",
    "memory_list",
    "memory_update",
    "memory_delete",
]


def derive_agent_tools(
    *,
    is_entry_point: bool,
    browser_enabled: bool,
    cron_enabled: bool,
    whatsapp_enabled: bool = False,
) -> tuple[list[str], list[str]]:
    """Derive the OpenClaw ``tools.allow`` / ``tools.deny`` lists for one agent.

    Returns ``(allow, deny)``. All capability tools are manifest-driven:

    - Entry point (supervisor): broad allow including ``group:fs`` (file access, always),
      ``process`` (background long-running commands instead of blocking the turn) and
      ``group:sessions`` + ``agents_list`` to inspect and drive its team — the last two
      unconditionally, even with an empty team (see the note at the grant site).
      ``cron`` only here, and only when enabled.
      ``whatsapp_login`` (in-chat QR pairing tool) only here, and only when WhatsApp is on.
    - Subagent: filesystem/exec/process/web + browser/pdf; ``cron`` is always denied.
    - Long-term memory tools (``_MEMORY_TOOLS``) are granted to every agent so the injected
      mem0 triage protocol can actually persist/look up facts inline (see the constant's note).
    - ``browser`` is present per agent only when ``browser_enabled``. The builtin
      ``image_generate`` / ``video_generate`` tools are **never** allowed here: media generation
      goes through sellerclaw-cli (cloud endpoints) and the builtins are denied in config
      generation, so adding them to ``allow`` only produces an "unknown tool" warning.
    """
    if is_entry_point:
        # ``agents_list`` lets the supervisor enumerate its available team alongside
        # ``group:sessions`` (spawn/list/manage live child sessions). Granted whether or not a
        # specialist is enabled right now: OpenClaw hot-applies a changed subagent roster
        # (``allowAgents``) to the running gateway — new and already-open sessions alike — but it
        # does NOT re-derive a changed per-agent tool allow-list. Gating these two on "the team
        # is non-empty" therefore made the *first* specialist unusable until the whole agent
        # restarted: the supervisor kept its empty-team tool set and could neither list nor spawn
        # anyone, so it promised the owner a specialist and then silently did the work itself.
        # Constant here, dynamic in ``allowAgents``: the roster stays the real gate, and that one
        # does reload live. An empty roster is a safe resting state — ``agents_list`` returns
        # nobody and ``sessions_spawn`` has no id it will accept (``requireAgentId``).
        #
        # The supervisor also gets file access (``group:fs``) unconditionally so it can read and
        # write workspace files itself, not only delegate.
        allow = [
            "group:sessions",
            "agents_list",
            "group:fs",
            "group:web",
            "web_search",
            "message",
            "browser",
            "exec",
            "pdf",
        ]
        if cron_enabled:
            allow.append("cron")
        # WhatsApp links a personal account by QR; the agent runs ``whatsapp_login`` in the
        # chat to render the QR image for the seller to scan. Only granted when the channel
        # is enabled so the tool surface stays minimal otherwise.
        if whatsapp_enabled:
            allow.append("whatsapp_login")
        # ``process`` lets the agent background long-running commands (publish, export, bulk
        # jobs) instead of running them synchronously and blocking the whole turn until the exec
        # timeout — a blocking publish call was stalling mid-turn and timing out. Granted to the
        # entry point whether or not it drives subagents: children inherit the parent's tool set,
        # so without it here OpenClaw also strips ``process`` from the subagents ("inherited
        # tools"), leaving no agent able to run work in the background.
        allow.append("process")
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
    # Every agent gets the long-term memory tools (entry point and subagents alike) so the mem0
    # triage protocol can persist/recall facts. Appended last → predictable, deterministic order.
    allow.extend(_MEMORY_TOOLS)
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


# Path the cloud API mounts its LiteLLM proxy on (src/litellm_proxy in the monolith).
_CLOUD_LITELLM_PROXY_PREFIX = "/litellm"


def _localize_cloud_proxy_url(base_url: str, *, sellerclaw_api_url: str) -> str:
    """Reach the cloud's ``/litellm`` proxy over the host we already talk to it on.

    The manifest carries whatever public URL the cloud advertises for itself. In local
    development that is an ngrok tunnel, so a box sitting next to the cloud API would send
    every model token out to the public internet and back — and a long streamed answer that
    loses that round trip dies mid-sentence ("terminated"), which the user sees as a turn
    that never finished.

    ``SELLERCLAW_API_URL`` is the address this agent already uses for every other cloud call
    (manifest pull, memory, web search), so borrowing its scheme+host keeps the request on the
    short path while still going THROUGH the cloud proxy — the path is preserved, so the
    request-shaping that route does (e.g. injecting Gemini's ``/v1beta`` segment) still applies.

    Only ``/litellm`` URLs are rewritten: a group pointed straight at LiteLLM (staging and
    production do that) is not proxied by the cloud API and must keep its own host. Same-host
    URLs come out unchanged, so this is a no-op everywhere the two already agree.
    """
    api_base = (sellerclaw_api_url or "").strip().rstrip("/")
    if not api_base:
        return base_url
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if path != _CLOUD_LITELLM_PROXY_PREFIX and not path.startswith(f"{_CLOUD_LITELLM_PROXY_PREFIX}/"):
        return base_url
    api_parsed = urlsplit(api_base)
    if not api_parsed.scheme or not api_parsed.netloc:
        return base_url
    return urlunsplit(
        (api_parsed.scheme, api_parsed.netloc, f"{api_parsed.path.rstrip('/')}{parsed.path}", "", "")
    )


def build_providers(manifest: GenericManifest, *, sellerclaw_api_url: str = "") -> dict[str, object]:
    """Build the OpenClaw ``models.providers`` mapping from ``manifest.llm.groups``.

    Each provider is built strictly from its own group: ``baseUrl`` / ``apiKey`` come from
    the group, ``api`` is emitted only when the group declares one, and the ``models[]`` list
    carries the group's (prefixed) model ids + metadata — minus image/video models.
    """
    media_refs = _media_model_refs(manifest.llm)
    providers: dict[str, object] = {}
    for name, group in manifest.llm.groups.items():
        entry: dict[str, object] = {
            "baseUrl": _localize_cloud_proxy_url(group.base_url, sellerclaw_api_url=sellerclaw_api_url),
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
            providers=build_providers(manifest, sellerclaw_api_url=sellerclaw_api_url),
            openclaw_version=get_openclaw_version(),
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
            heartbeat_every=manifest.agents.heartbeat_every,
            cron_enabled=manifest.cron_enabled,
            web_fetch_enabled=manifest.web_fetch_enabled,
            memory_enabled=manifest.memory_enabled,
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
            skill_references=content.skill_references_mapping(),
        )
