from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sellerclaw_agent.bundle.manifest import (
    ModelRef,
    WebSearchManifest,
    bundle_manifest_from_mapping,
)
from sellerclaw_agent.models import ModelTier
from sellerclaw_agent.test_manifest_fixtures import load_manifest_v2_mapping

pytestmark = pytest.mark.unit

_EXPECTED_USER_ID = UUID("5fdc144e-fe03-4339-9b43-5bd62b99bfac")


def _v2() -> dict[str, Any]:
    return copy.deepcopy(load_manifest_v2_mapping())


def test_parse_happy_path_from_fixture() -> None:
    manifest = bundle_manifest_from_mapping(_v2())
    assert manifest.user_id == _EXPECTED_USER_ID
    assert manifest.agent_api_base_path == "/agent"
    assert manifest.web_search == WebSearchManifest(enabled=False)
    # LiteLLM group + prefix helpers.
    group = manifest.litellm_group()
    assert group is not None
    assert group.base_url == "https://example.ngrok-free.dev/litellm"
    assert manifest.model_name_prefix == "u:5fdc144e/"
    # Agents: main + 3 subagents, with resolved entry-point + subagent ids.
    assert manifest.agents.main_agent.id == "supervisor"
    assert manifest.agents.main_agent.is_entry_point is True
    assert manifest.agents.main_agent.subagent_ids == ("scout", "supplier", "marketing")
    assert tuple(s.id for s in manifest.agents.subagents) == ("scout", "supplier", "marketing")


def test_parse_resolves_default_flags_for_subagents() -> None:
    """scout has thinking:null -> falls back to agents.thinking_default ('adaptive')."""
    manifest = bundle_manifest_from_mapping(_v2())
    by_id = {s.id: s for s in manifest.agents.subagents}
    assert by_id["scout"].thinking == "adaptive"
    assert by_id["supplier"].thinking == "off"
    # main agent image/video default to True even though agents-level defaults are False.
    assert manifest.agents.main_agent.image_generation is True
    assert manifest.agents.main_agent.video_generation is True
    # supplier has browser_enabled:false explicitly.
    assert by_id["supplier"].browser_enabled is False
    assert by_id["scout"].browser_enabled is True


def test_parse_reasoning_default_from_fixture_and_absent() -> None:
    """``agents.reasoning_default`` parses from the fixture ('on') and falls back to 'off'."""
    assert bundle_manifest_from_mapping(_v2()).agents.reasoning_default == "on"

    data = _v2()
    data["agents"].pop("reasoning_default", None)
    assert bundle_manifest_from_mapping(data).agents.reasoning_default == "off"


def test_parse_model_info_optional_sizing_fields() -> None:
    """Only the frontier model carries reasoning/context/output sizing; the rest are None."""
    manifest = bundle_manifest_from_mapping(_v2())
    models = {m.id: m for m in manifest.llm.groups["litellm"].models}
    complex_model = models["complex"]
    assert complex_model.reasoning is True
    assert complex_model.context_window == 256000
    assert complex_model.max_tokens == 32768
    for non_complex in ("simple", "mini", "image", "video"):
        entry = models[non_complex]
        assert entry.reasoning is None
        assert entry.context_window is None
        assert entry.max_tokens is None


def test_model_info_optional_fields_round_trip_omit_when_unset() -> None:
    """Saving + reparsing keeps the sizing keys only on the model that declared them."""
    manifest = bundle_manifest_from_mapping(_v2())
    mapping = manifest.to_save_manifest_mapping()
    litellm_models = {m["id"]: m for m in mapping["llm"]["groups"]["litellm"]["models"]}  # type: ignore[index]
    assert litellm_models["complex"]["contextWindow"] == 256000
    assert litellm_models["complex"]["maxTokens"] == 32768
    assert litellm_models["complex"]["reasoning"] is True
    for non_complex in ("simple", "mini", "image", "video"):
        entry = litellm_models[non_complex]
        assert "reasoning" not in entry
        assert "contextWindow" not in entry
        assert "maxTokens" not in entry


def test_parse_agent_content_and_skills() -> None:
    manifest = bundle_manifest_from_mapping(_v2())
    content = manifest.agents.main_agent.content
    assert content.instructions.startswith("# Supervisor")
    assert content.soul is not None and content.soul.startswith("# Soul")
    assert content.identity is None
    assert content.heartbeat is not None
    skills = content.skills_mapping()
    assert set(skills) == {"task-management", "tasks"}
    assert "Use the tasks API" in skills["task-management"]


def test_parse_telegram_channels() -> None:
    manifest = bundle_manifest_from_mapping(_v2())
    assert manifest.channels.primary == "telegram"
    tg = manifest.channels.telegram
    assert tg.enabled is True
    assert tg.bot_token == "1234567890"
    assert tg.allowed_user_ids == ("1234567890",)
    assert tg.allowed_group_ids == ("1234567891",)


@pytest.mark.parametrize(
    ("mutate", "exc", "match"),
    [
        pytest.param(
            lambda d: d.pop("user_id"),
            ValueError,
            "user_id is required",
            id="missing-user-id",
        ),
        pytest.param(
            lambda d: d.__setitem__("user_id", "not-a-uuid"),
            ValueError,
            "badly formed hexadecimal UUID string",
            id="invalid-user-id",
        ),
        pytest.param(
            lambda d: d.__setitem__("llm", "not-a-mapping"),
            ValueError,
            "llm must be a mapping",
            id="llm-not-mapping",
        ),
        pytest.param(
            lambda d: d["llm"].__setitem__("text_model", {}),
            ValueError,
            "text_model.primary is required",
            id="missing-text-primary",
        ),
        pytest.param(
            lambda d: d["agents"].__setitem__("subagents", "x"),
            TypeError,
            "subagents must be a list",
            id="subagents-not-list",
        ),
        pytest.param(
            lambda d: d["agents"]["main_agent"]["content"].__setitem__("instructions", ""),
            ValueError,
            "instructions must not be empty",
            id="empty-instructions",
        ),
        pytest.param(
            lambda d: d.__setitem__("agent_api_base_path", "agent"),
            ValueError,
            "agent_api_base_path must start with '/'",
            id="bad-base-path",
        ),
        pytest.param(
            lambda d: d.__setitem__("agent_api_base_path", 123),
            TypeError,
            "agent_api_base_path must be a string",
            id="non-string-base-path",
        ),
        pytest.param(
            lambda d: d["llm"]["groups"]["litellm"].__setitem__("models", "x"),
            TypeError,
            "models must be a list",
            id="group-models-not-list",
        ),
    ],
)
def test_parse_malformed_input_raises(
    mutate: Callable[[dict[str, Any]], Any],
    exc: type[Exception],
    match: str,
) -> None:
    data = _v2()
    mutate(data)
    with pytest.raises(exc, match=match):
        bundle_manifest_from_mapping(data)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        pytest.param(lambda d: d.pop("agent_api_base_path"), "agent_api_base_path is required", id="missing-base-path"),
        pytest.param(lambda d: d.pop("channels"), "channels is required", id="missing-channels"),
        pytest.param(lambda d: d.pop("llm"), "llm is required", id="missing-llm"),
        pytest.param(lambda d: d.pop("agents"), "agents is required", id="missing-agents"),
        pytest.param(lambda d: d.__setitem__("web_search", {}), "web_search.enabled is required", id="web-search-no-enabled"),
        pytest.param(lambda d: d.__setitem__("web_search", {"enabled": "yes"}), "web_search.enabled must be a boolean", id="web-search-enabled-not-bool"),
        pytest.param(lambda d: d["channels"].pop("primary"), "channels.primary is required", id="missing-primary"),
        pytest.param(lambda d: d["channels"].__setitem__("primary", "discord"), "channels.primary must be one of", id="primary-bad-enum"),
        pytest.param(lambda d: d["channels"]["telegram"].__setitem__("enabled", False), "telegram.enabled must be true", id="primary-telegram-but-disabled"),
        pytest.param(lambda d: d["channels"].pop("telegram"), "channels.telegram is required when", id="primary-telegram-no-telegram"),
        pytest.param(lambda d: d["channels"]["telegram"].__setitem__("bot_token", ""), "bot_token is required", id="telegram-enabled-no-token"),
        pytest.param(lambda d: d["channels"]["telegram"].pop("allowed_user_ids"), "allowed_user_ids is required", id="telegram-enabled-no-users"),
        pytest.param(lambda d: d["channels"]["telegram"].__setitem__("allowed_user_ids", "1"), "allowed_user_ids must be a list", id="telegram-users-not-list"),
        pytest.param(lambda d: d["llm"].pop("groups"), "llm.groups is required", id="missing-groups"),
        pytest.param(lambda d: d["llm"]["text_model"].pop("secondary"), "llm.text_model.secondary is required", id="missing-text-secondary"),
        pytest.param(lambda d: d["llm"]["text_model"]["primary"].__setitem__("group", "ghost"), "references unknown group", id="ref-unknown-group"),
        pytest.param(lambda d: d["llm"]["text_model"]["primary"].__setitem__("model", "ghost"), "not declared in group", id="ref-unknown-model"),
        pytest.param(lambda d: d["agents"]["subagents"][0].__setitem__("id", "supervisor"), "must be unique", id="duplicate-agent-id"),
        pytest.param(lambda d: d["agents"].__setitem__("model_default", "tertiary"), "model_default must be", id="bad-model-default"),
        # Media blocks: present block requires a 'primary' ref.
        pytest.param(lambda d: d["llm"]["image_model"].pop("primary"), "llm.image_model.primary is required", id="image-model-no-primary"),
        pytest.param(lambda d: d["llm"]["video_model"].pop("primary"), "llm.video_model.primary is required", id="video-model-no-primary"),
        pytest.param(lambda d: d["llm"]["pdf_model"].pop("primary"), "llm.pdf_model.primary is required", id="pdf-model-no-primary"),
        # compaction / memory_flush refs resolve against groups (same rule).
        pytest.param(lambda d: d["llm"]["compaction_model"].__setitem__("group", "ghost"), "references unknown group", id="compaction-unknown-group"),
        pytest.param(lambda d: d["llm"]["memory_flush_model"].__setitem__("model", "ghost"), "not declared in group", id="memory-flush-unknown-model"),
        # Provider group required fields.
        pytest.param(lambda d: d["llm"]["groups"]["litellm"].pop("base_url"), "base_url is required", id="group-no-base-url"),
        pytest.param(lambda d: d["llm"]["groups"]["litellm"].__setitem__("base_url", "not-a-url"), "must be a valid http", id="group-bad-base-url"),
        pytest.param(lambda d: d["llm"]["groups"]["litellm"].pop("api_key"), "api_key is required", id="group-no-api-key"),
        pytest.param(lambda d: d["llm"]["groups"]["litellm"].__setitem__("api_key", ""), "api_key must not be empty", id="group-empty-api-key"),
        pytest.param(lambda d: d["llm"]["groups"]["litellm"].pop("models"), "models is required", id="group-no-models"),
        pytest.param(lambda d: d["llm"]["groups"]["litellm"].__setitem__("models", []), "models must not be empty", id="group-empty-models"),
        pytest.param(lambda d: d["llm"]["groups"]["litellm"].__setitem__("models", ["x"]), "must be a mapping", id="group-models-not-dict"),
        pytest.param(lambda d: d["llm"]["groups"]["litellm"]["models"][0].pop("id"), "id is required", id="model-no-id"),
        pytest.param(lambda d: d.__setitem__("cron", {}), "cron.enabled is required", id="cron-no-enabled"),
        pytest.param(lambda d: d.__setitem__("web_fetch", {"enabled": "yes"}), "web_fetch.enabled must be a boolean", id="web-fetch-enabled-not-bool"),
    ],
)
def test_parse_additional_validation_raises(
    mutate: Callable[[dict[str, Any]], Any],
    match: str,
) -> None:
    data = _v2()
    mutate(data)
    with pytest.raises(ValueError, match=match):
        bundle_manifest_from_mapping(data)


def test_optional_media_and_aux_model_blocks_absent_ok() -> None:
    """image/video/pdf/compaction/memory_flush are optional; absence -> None refs, no error."""
    data = _v2()
    for key in ("image_model", "video_model", "pdf_model", "compaction_model", "memory_flush_model"):
        data["llm"].pop(key, None)
    manifest = bundle_manifest_from_mapping(data)
    assert manifest.llm.image_model_primary is None
    assert manifest.llm.video_model_primary is None
    assert manifest.llm.pdf_model_primary is None
    assert manifest.llm.compaction_model is None
    assert manifest.llm.memory_flush_model is None
    # text_model + groups still drive the bundle.
    assert manifest.llm.text_model["primary"].model == "complex"


def test_present_media_blocks_resolve_group_and_model() -> None:
    manifest = bundle_manifest_from_mapping(_v2())
    assert manifest.llm.image_model_primary == ModelRef(group="litellm", model="image")
    assert manifest.llm.video_model_primary == ModelRef(
        group="google", model="veo-3.1-fast-generate-preview"
    )
    assert manifest.llm.pdf_model_primary == ModelRef(group="anthropic", model="claude-sonnet-4-6")
    assert manifest.llm.pdf_model_fallbacks == (
        ModelRef(group="google", model="gemini-3.1-pro-preview"),
    )


def test_defaults_applied_when_optional_fields_absent() -> None:
    data = _v2()
    data.pop("web_search")
    data.pop("proxy_url", None)
    # primary=telegram requires telegram; switch to sellerclaw-ui to drop telegram safely.
    data["channels"]["primary"] = "sellerclaw-ui"
    data["channels"].pop("telegram")
    manifest = bundle_manifest_from_mapping(data)
    assert manifest.web_search == WebSearchManifest(enabled=False)
    assert manifest.resolved_proxy_url == ""
    assert manifest.channels.telegram.enabled is False
    assert manifest.channels.telegram.bot_token == ""
    # cron / web_fetch default to enabled when the manifest omits them.
    assert manifest.cron_enabled is True
    assert manifest.web_fetch_enabled is True


def test_to_save_manifest_mapping_roundtrips() -> None:
    manifest = bundle_manifest_from_mapping(_v2())
    mapping = manifest.to_save_manifest_mapping()
    again = bundle_manifest_from_mapping(mapping)
    assert again.user_id == manifest.user_id
    assert again.agent_api_base_path == manifest.agent_api_base_path
    assert again.proxy_url == manifest.proxy_url
    assert again.web_search == manifest.web_search
    assert again.model_name_prefix == manifest.model_name_prefix
    assert [s.id for s in again.agents.subagents] == [s.id for s in manifest.agents.subagents]
    assert again.agents.main_agent.content.instructions == manifest.agents.main_agent.content.instructions
    assert again.channels.telegram.bot_token == manifest.channels.telegram.bot_token


def test_resolved_proxy_url_strips_whitespace() -> None:
    data = _v2()
    data["proxy_url"] = "  http://proxy.example:3128  "
    manifest = bundle_manifest_from_mapping(data)
    assert manifest.resolved_proxy_url == "http://proxy.example:3128"


def test_model_tier_enum_values() -> None:
    """ModelTier still drives complex vs simple group selection downstream."""
    assert ModelTier.COMPLEX.value == "complex"
    assert ModelTier.SIMPLE.value == "simple"


def test_group_api_field_parses_when_present_and_defaults_none() -> None:
    """The litellm group declares ``api``; the passthrough groups omit it (-> None)."""
    manifest = bundle_manifest_from_mapping(_v2())
    assert manifest.llm.groups["litellm"].api == "openai-completions"
    assert manifest.llm.groups["anthropic"].api is None
    assert manifest.llm.groups["google"].api is None


def test_group_api_field_round_trips() -> None:
    """``api`` is preserved on save->reparse only for the group that carries it."""
    manifest = bundle_manifest_from_mapping(_v2())
    mapping = manifest.to_save_manifest_mapping()
    # Emitted only when set.
    llm_groups = mapping["llm"]["groups"]  # type: ignore[index]
    assert "api" in llm_groups["litellm"]
    assert "api" not in llm_groups["anthropic"]
    again = bundle_manifest_from_mapping(mapping)
    assert again.llm.groups["litellm"].api == "openai-completions"
    assert again.llm.groups["anthropic"].api is None


def test_group_api_must_be_string_when_provided() -> None:
    data = _v2()
    data["llm"]["groups"]["litellm"]["api"] = 123
    with pytest.raises(ValueError, match="api must be a string"):
        bundle_manifest_from_mapping(data)


def test_agent_without_model_inherits_agents_model_default() -> None:
    """An agent omitting ``model`` resolves to ``agents.model_default`` (here 'secondary')."""
    data = _v2()
    data["agents"]["model_default"] = "secondary"
    # scout omits its own model -> should inherit the agents-level default.
    data["agents"]["subagents"][0].pop("model", None)
    manifest = bundle_manifest_from_mapping(data)
    by_id = {s.id: s for s in manifest.agents.subagents}
    assert by_id["scout"].model == "secondary"
    # supplier still has an explicit model -> unchanged.
    assert by_id["supplier"].model == "secondary"
    # An agent that explicitly sets 'primary' is unaffected by the default.
    assert by_id["marketing"].model == "primary"
