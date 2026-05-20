from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class ModelRef:
    """Reference to a concrete model within a provider group (e.g. ``litellm/complex``)."""

    group: str
    model: str


@dataclass(frozen=True)
class ModelInfo:
    """OpenClaw model metadata entry inside a provider group."""

    id: str
    name: str
    reasoning: bool
    input: tuple[str, ...]
    context_window: int
    max_tokens: int


@dataclass(frozen=True)
class ModelGroup:
    """A provider group: base URL + key + the models it exposes."""

    base_url: str
    api_key: str
    model_name_prefix: str
    models: tuple[ModelInfo, ...]


@dataclass(frozen=True)
class LlmManifest:
    """LLM routing block: per-role model refs plus provider groups."""

    text_model: dict[str, ModelRef]
    image_model_primary: ModelRef | None
    image_model_fallbacks: tuple[ModelRef, ...]
    video_model_primary: ModelRef | None
    video_model_fallbacks: tuple[ModelRef, ...]
    pdf_model_primary: ModelRef | None
    pdf_model_fallbacks: tuple[ModelRef, ...]
    compaction_model: ModelRef | None
    memory_flush_model: ModelRef | None
    groups: dict[str, ModelGroup]


@dataclass(frozen=True)
class AgentContent:
    """Pre-rendered per-agent prompt content (assembled upstream)."""

    instructions: str
    soul: str | None = None
    identity: str | None = None
    user_context: str | None = None
    tools_doc: str | None = None
    heartbeat: str | None = None
    skills: tuple[tuple[str, str], ...] = ()

    def skills_mapping(self) -> dict[str, str]:
        return {name: content for name, content in self.skills}


@dataclass(frozen=True)
class AgentSpec:
    """One agent with resolved flags and pre-rendered content."""

    id: str
    name: str
    model: str
    is_entry_point: bool
    subagent_ids: tuple[str, ...]
    thinking: str | None
    browser_enabled: bool
    image_generation: bool
    video_generation: bool
    content: AgentContent


@dataclass(frozen=True)
class TelegramManifest:
    enabled: bool = False
    bot_token: str = ""
    allowed_user_ids: tuple[str, ...] = ()
    allowed_group_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class WebSearchManifest:
    """Web search is configured only on the monolith; the manifest carries a single toggle."""

    enabled: bool = False


@dataclass(frozen=True)
class ChannelsManifest:
    primary: str = "sellerclaw-ui"
    telegram: TelegramManifest = field(default_factory=TelegramManifest)


@dataclass(frozen=True)
class AgentsManifest:
    """Agents block: defaults + the main agent + ordered subagents."""

    thinking_default: str
    model_default: str
    browser_enabled_default: bool
    image_generation_default: bool
    video_generation_default: bool
    main_agent: AgentSpec
    subagents: tuple[AgentSpec, ...]


@dataclass(frozen=True)
class GenericManifest:
    """Generic agent manifest: pre-rendered per-agent content + LLM/channel wiring.

    The agent generates the OpenClaw config and workspaces purely from this object;
    it no longer reads on-disk templates.
    """

    user_id: UUID
    agent_api_base_path: str
    proxy_url: str
    web_search: WebSearchManifest
    llm: LlmManifest
    agents: AgentsManifest
    channels: ChannelsManifest
    raw: dict[str, object] = field(default_factory=dict)

    def litellm_group(self) -> ModelGroup | None:
        """Return the provider group named ``litellm`` (None if absent)."""
        return self.llm.groups.get("litellm")

    @property
    def model_name_prefix(self) -> str:
        """LiteLLM model-name prefix (``""`` when there is no litellm group)."""
        group = self.litellm_group()
        return group.model_name_prefix if group is not None else ""

    @property
    def resolved_proxy_url(self) -> str:
        return (self.proxy_url or "").strip()

    def all_agents(self) -> list[AgentSpec]:
        return [self.agents.main_agent, *self.agents.subagents]

    def to_save_manifest_mapping(self) -> dict[str, object]:
        """Round-trippable dict shape accepted by ``bundle_manifest_from_mapping``."""
        return {
            "user_id": str(self.user_id),
            "agent_api_base_path": self.agent_api_base_path,
            "proxy_url": self.proxy_url,
            "web_search": {"enabled": self.web_search.enabled},
            "llm": _llm_to_mapping(self.llm),
            "agents": _agents_to_mapping(self.agents),
            "channels": {
                "primary": self.channels.primary,
                "telegram": {
                    "enabled": self.channels.telegram.enabled,
                    "bot_token": self.channels.telegram.bot_token,
                    "allowed_user_ids": list(self.channels.telegram.allowed_user_ids),
                    "allowed_group_ids": list(self.channels.telegram.allowed_group_ids),
                },
            },
        }


def _tuple_str(v: object) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,) if v.strip() else ()
    if isinstance(v, (list, tuple)):
        return tuple(str(x).strip() for x in v if str(x).strip())
    raise TypeError(f"Expected list or str, got {type(v)}")


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return {str(k): v for k, v in value.items()}


def _parse_model_ref(value: object, label: str) -> ModelRef:
    data = _require_mapping(value, label)
    group = str(data.get("group", "")).strip()
    model = str(data.get("model", "")).strip()
    if not group or not model:
        raise ValueError(f"{label} requires non-empty 'group' and 'model'")
    return ModelRef(group=group, model=model)


def _parse_optional_model_ref(value: object, label: str) -> ModelRef | None:
    if value is None:
        return None
    return _parse_model_ref(value, label)


def _parse_model_ref_list(value: object, label: str) -> tuple[ModelRef, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{label} must be a list")
    return tuple(_parse_model_ref(item, f"{label}[]") for item in value)


def _coerce_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f"Expected int-like value, got {type(value)}")


def _parse_model_info(value: object, label: str) -> ModelInfo:
    data = _require_mapping(value, label)
    return ModelInfo(
        id=str(data["id"]),
        name=str(data.get("name", data["id"])),
        reasoning=bool(data.get("reasoning", False)),
        input=_tuple_str(data.get("input")),
        context_window=_coerce_int(data.get("contextWindow", 0)),
        max_tokens=_coerce_int(data.get("maxTokens", 0)),
    )


def _parse_model_group(value: object, label: str) -> ModelGroup:
    data = _require_mapping(value, label)
    models_raw = data.get("models") or []
    if not isinstance(models_raw, (list, tuple)):
        raise TypeError(f"{label}.models must be a list")
    return ModelGroup(
        base_url=str(data.get("base_url", "")),
        api_key=str(data.get("api_key", "")),
        model_name_prefix=str(data.get("model_name_prefix") or ""),
        models=tuple(_parse_model_info(m, f"{label}.models[]") for m in models_raw),
    )


def _parse_llm(value: object) -> LlmManifest:
    data = _require_mapping(value, "llm")

    text_raw = _require_mapping(data.get("text_model"), "llm.text_model")
    text_model: dict[str, ModelRef] = {}
    for role in ("primary", "secondary"):
        if role in text_raw and text_raw[role] is not None:
            text_model[role] = _parse_model_ref(text_raw[role], f"llm.text_model.{role}")
    if "primary" not in text_model:
        raise ValueError("llm.text_model.primary is required")
    text_model.setdefault("secondary", text_model["primary"])

    image_raw = _require_mapping(data.get("image_model") or {}, "llm.image_model")
    video_raw = _require_mapping(data.get("video_model") or {}, "llm.video_model")
    pdf_raw = _require_mapping(data.get("pdf_model") or {}, "llm.pdf_model")

    groups_raw = _require_mapping(data.get("groups") or {}, "llm.groups")
    groups = {
        name: _parse_model_group(group, f"llm.groups.{name}")
        for name, group in groups_raw.items()
    }

    return LlmManifest(
        text_model=text_model,
        image_model_primary=_parse_optional_model_ref(
            image_raw.get("primary"), "llm.image_model.primary"
        ),
        image_model_fallbacks=_parse_model_ref_list(
            image_raw.get("fallbacks"), "llm.image_model.fallbacks"
        ),
        video_model_primary=_parse_optional_model_ref(
            video_raw.get("primary"), "llm.video_model.primary"
        ),
        video_model_fallbacks=_parse_model_ref_list(
            video_raw.get("fallbacks"), "llm.video_model.fallbacks"
        ),
        pdf_model_primary=_parse_optional_model_ref(
            pdf_raw.get("primary"), "llm.pdf_model.primary"
        ),
        pdf_model_fallbacks=_parse_model_ref_list(
            pdf_raw.get("fallbacks"), "llm.pdf_model.fallbacks"
        ),
        compaction_model=_parse_optional_model_ref(
            data.get("compaction_model"), "llm.compaction_model"
        ),
        memory_flush_model=_parse_optional_model_ref(
            data.get("memory_flush_model"), "llm.memory_flush_model"
        ),
        groups=groups,
    )


def _parse_agent_content(value: object, label: str) -> AgentContent:
    data = _require_mapping(value, label)
    instructions = str(data.get("instructions") or "")
    if not instructions.strip():
        raise ValueError(f"{label}.instructions must not be empty")

    skills_raw = data.get("skills") or []
    if not isinstance(skills_raw, (list, tuple)):
        raise TypeError(f"{label}.skills must be a list")
    skills: list[tuple[str, str]] = []
    for skill in skills_raw:
        skill_map = _require_mapping(skill, f"{label}.skills[]")
        name = str(skill_map.get("name") or "").strip()
        content = str(skill_map.get("content") or "")
        if not name:
            raise ValueError(f"{label}.skills[].name must not be empty")
        skills.append((name, content))

    return AgentContent(
        instructions=instructions,
        soul=_optional_str(data.get("soul")),
        identity=_optional_str(data.get("identity")),
        user_context=_optional_str(data.get("user_context")),
        tools_doc=_optional_str(data.get("tools_doc")),
        heartbeat=_optional_str(data.get("heartbeat")),
        skills=tuple(skills),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def _optional_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _optional_thinking(value: object, default: str) -> str | None:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _parse_agent_spec(
    value: object,
    label: str,
    *,
    is_entry_point: bool,
    subagent_ids: tuple[str, ...],
    thinking_default: str,
    browser_enabled_default: bool,
    image_generation_default: bool,
    video_generation_default: bool,
) -> AgentSpec:
    data = _require_mapping(value, label)
    agent_id = str(data.get("id") or "").strip()
    name = str(data.get("name") or "").strip()
    if not agent_id:
        raise ValueError(f"{label}.id must not be empty")
    if not name:
        raise ValueError(f"{label}.name must not be empty")
    model = str(data.get("model") or "primary").strip() or "primary"
    if model not in ("primary", "secondary"):
        raise ValueError(f"{label}.model must be 'primary' or 'secondary', got {model!r}")

    # Entry point (main agent) defaults image/video generation to True; subagents
    # fall back to the agents-level defaults. Browser falls back to the default for both.
    image_default = True if is_entry_point else image_generation_default
    video_default = True if is_entry_point else video_generation_default

    return AgentSpec(
        id=agent_id,
        name=name,
        model=model,
        is_entry_point=is_entry_point,
        subagent_ids=subagent_ids,
        thinking=_optional_thinking(data.get("thinking"), thinking_default),
        browser_enabled=_optional_bool(data.get("browser_enabled"), browser_enabled_default),
        image_generation=_optional_bool(data.get("image_generation"), image_default),
        video_generation=_optional_bool(data.get("video_generation"), video_default),
        content=_parse_agent_content(data.get("content"), f"{label}.content"),
    )


def _parse_agents(value: object) -> AgentsManifest:
    data = _require_mapping(value, "agents")
    thinking_default = str(data.get("thinking_default") or "adaptive").strip() or "adaptive"
    model_default = str(data.get("model_default") or "primary").strip() or "primary"
    browser_enabled_default = bool(data.get("browser_enabled_default", True))
    image_generation_default = bool(data.get("image_generation_default", False))
    video_generation_default = bool(data.get("video_generation_default", False))

    subagents_raw = data.get("subagents") or []
    if not isinstance(subagents_raw, (list, tuple)):
        raise TypeError("agents.subagents must be a list")
    subagent_specs = [
        _parse_agent_spec(
            sub,
            f"agents.subagents[{i}]",
            is_entry_point=False,
            subagent_ids=(),
            thinking_default=thinking_default,
            browser_enabled_default=browser_enabled_default,
            image_generation_default=image_generation_default,
            video_generation_default=video_generation_default,
        )
        for i, sub in enumerate(subagents_raw)
    ]
    subagent_ids = tuple(spec.id for spec in subagent_specs)

    main_agent = _parse_agent_spec(
        data.get("main_agent"),
        "agents.main_agent",
        is_entry_point=True,
        subagent_ids=subagent_ids,
        thinking_default=thinking_default,
        browser_enabled_default=browser_enabled_default,
        image_generation_default=image_generation_default,
        video_generation_default=video_generation_default,
    )

    return AgentsManifest(
        thinking_default=thinking_default,
        model_default=model_default,
        browser_enabled_default=browser_enabled_default,
        image_generation_default=image_generation_default,
        video_generation_default=video_generation_default,
        main_agent=main_agent,
        subagents=tuple(subagent_specs),
    )


def _parse_channels(value: object) -> ChannelsManifest:
    data = _require_mapping(value or {}, "channels")
    tg_raw = data.get("telegram") or {}
    if not isinstance(tg_raw, dict):
        raise ValueError("channels.telegram must be a mapping")
    telegram = TelegramManifest(
        enabled=bool(tg_raw.get("enabled", False)),
        bot_token=str(tg_raw.get("bot_token", "")),
        allowed_user_ids=_tuple_str(tg_raw.get("allowed_user_ids")),
        allowed_group_ids=_tuple_str(tg_raw.get("allowed_group_ids")),
    )
    return ChannelsManifest(
        primary=str(data.get("primary") or "sellerclaw-ui").strip() or "sellerclaw-ui",
        telegram=telegram,
    )


def _normalize_agent_api_base_path(value: object) -> str:
    """Accept str/None, strip whitespace, require leading slash when non-empty."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError("agent_api_base_path must be a string")
    normalized = value.strip().rstrip("/")
    if not normalized:
        return ""
    if not normalized.startswith("/"):
        raise ValueError(
            f"agent_api_base_path must start with '/' when non-empty, got {normalized!r}"
        )
    return normalized


def bundle_manifest_from_mapping(data: dict[str, object]) -> GenericManifest:
    """Build :class:`GenericManifest` from a plain dict (the v2 contract).

    Raises ``ValueError``/``TypeError`` on malformed input.
    """
    if "user_id" not in data:
        raise ValueError("user_id is required")

    ws_raw = data.get("web_search") or {}
    if not isinstance(ws_raw, dict):
        raise ValueError("web_search must be a mapping")
    web_search = WebSearchManifest(enabled=bool(ws_raw.get("enabled", False)))

    return GenericManifest(
        user_id=UUID(str(data["user_id"])),
        agent_api_base_path=_normalize_agent_api_base_path(data.get("agent_api_base_path")),
        proxy_url=str(data.get("proxy_url") or "").strip(),
        web_search=web_search,
        llm=_parse_llm(data.get("llm")),
        agents=_parse_agents(data.get("agents")),
        channels=_parse_channels(data.get("channels")),
        raw={str(k): v for k, v in data.items()},
    )


def _model_ref_to_mapping(ref: ModelRef) -> dict[str, str]:
    return {"group": ref.group, "model": ref.model}


def _model_info_to_mapping(info: ModelInfo) -> dict[str, object]:
    return {
        "id": info.id,
        "name": info.name,
        "reasoning": info.reasoning,
        "input": list(info.input),
        "contextWindow": info.context_window,
        "maxTokens": info.max_tokens,
    }


def _model_group_to_mapping(group: ModelGroup) -> dict[str, object]:
    return {
        "base_url": group.base_url,
        "api_key": group.api_key,
        "model_name_prefix": group.model_name_prefix,
        "models": [_model_info_to_mapping(m) for m in group.models],
    }


def _llm_to_mapping(llm: LlmManifest) -> dict[str, object]:
    out: dict[str, object] = {
        "text_model": {
            role: _model_ref_to_mapping(ref) for role, ref in llm.text_model.items()
        },
        "image_model": {
            "primary": _model_ref_to_mapping(llm.image_model_primary)
            if llm.image_model_primary is not None
            else None,
            "fallbacks": [_model_ref_to_mapping(r) for r in llm.image_model_fallbacks],
        },
        "video_model": {
            "primary": _model_ref_to_mapping(llm.video_model_primary)
            if llm.video_model_primary is not None
            else None,
            "fallbacks": [_model_ref_to_mapping(r) for r in llm.video_model_fallbacks],
        },
        "pdf_model": {
            "primary": _model_ref_to_mapping(llm.pdf_model_primary)
            if llm.pdf_model_primary is not None
            else None,
            "fallbacks": [_model_ref_to_mapping(r) for r in llm.pdf_model_fallbacks],
        },
        "compaction_model": _model_ref_to_mapping(llm.compaction_model)
        if llm.compaction_model is not None
        else None,
        "memory_flush_model": _model_ref_to_mapping(llm.memory_flush_model)
        if llm.memory_flush_model is not None
        else None,
        "groups": {
            name: _model_group_to_mapping(group) for name, group in llm.groups.items()
        },
    }
    return out


def _agent_content_to_mapping(content: AgentContent) -> dict[str, object]:
    return {
        "instructions": content.instructions,
        "soul": content.soul,
        "identity": content.identity,
        "user_context": content.user_context,
        "tools_doc": content.tools_doc,
        "heartbeat": content.heartbeat,
        "skills": [{"name": name, "content": body} for name, body in content.skills],
    }


def _agent_spec_to_mapping(spec: AgentSpec) -> dict[str, object]:
    return {
        "id": spec.id,
        "name": spec.name,
        "model": spec.model,
        "thinking": spec.thinking,
        "browser_enabled": spec.browser_enabled,
        "image_generation": spec.image_generation,
        "video_generation": spec.video_generation,
        "content": _agent_content_to_mapping(spec.content),
    }


def _agents_to_mapping(agents: AgentsManifest) -> dict[str, object]:
    return {
        "thinking_default": agents.thinking_default,
        "model_default": agents.model_default,
        "browser_enabled_default": agents.browser_enabled_default,
        "image_generation_default": agents.image_generation_default,
        "video_generation_default": agents.video_generation_default,
        "main_agent": _agent_spec_to_mapping(agents.main_agent),
        "subagents": [_agent_spec_to_mapping(s) for s in agents.subagents],
    }
