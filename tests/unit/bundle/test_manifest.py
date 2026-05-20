from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sellerclaw_agent.bundle.manifest import (
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
