from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import pytest
from pydantic import ValidationError
from sellerclaw_agent.bundle.manifest import bundle_manifest_from_mapping
from sellerclaw_agent.server.schemas import SaveManifestRequest

pytestmark = pytest.mark.unit


def test_save_manifest_request_to_mapping_passes_bundle_validation(
    make_save_manifest_request: Callable[..., SaveManifestRequest],
) -> None:
    body = make_save_manifest_request()
    manifest = bundle_manifest_from_mapping(body.to_mapping())
    assert manifest.user_id == UUID("11111111-1111-4111-8111-111111111111")
    assert manifest.agents.main_agent.id == "supervisor"


@pytest.mark.parametrize(
    "bad_user_id",
    [
        pytest.param("not-a-uuid", id="invalid-uuid-string"),
        pytest.param("", id="empty-uuid"),
    ],
)
def test_save_manifest_request_rejects_invalid_user_id(
    make_save_manifest_request: Callable[..., SaveManifestRequest],
    bad_user_id: str,
) -> None:
    data = make_save_manifest_request().model_dump(mode="json")
    data["user_id"] = bad_user_id
    with pytest.raises(ValidationError):
        SaveManifestRequest.model_validate(data)


def test_save_manifest_request_requires_llm_and_agents(
    make_save_manifest_request: Callable[..., SaveManifestRequest],
) -> None:
    data = make_save_manifest_request().model_dump(mode="json")
    del data["agents"]
    with pytest.raises(ValidationError):
        SaveManifestRequest.model_validate(data)


def test_save_manifest_request_web_search_ignores_legacy_wire_fields(
    make_save_manifest_request: Callable[..., SaveManifestRequest],
) -> None:
    body = make_save_manifest_request()
    raw = body.model_dump(mode="json")
    raw["web_search"] = {
        "enabled": True,
        "provider": "brave",
        "api_key": "must-not-round-trip",
        "base_url": "https://legacy.example",
    }
    parsed = SaveManifestRequest.model_validate(raw)
    assert parsed.web_search is not None
    assert parsed.web_search.enabled is True
    again = bundle_manifest_from_mapping(parsed.to_mapping())
    assert again.web_search.enabled is True
    ws = parsed.to_mapping()["web_search"]
    assert isinstance(ws, dict)
    assert ws == {"enabled": True}


@pytest.mark.parametrize(
    ("drop_field", "make_invalid"),
    [
        pytest.param("agent_api_base_path", None, id="missing-agent-api-base-path"),
        pytest.param("channels", None, id="missing-channels"),
        pytest.param("llm", None, id="missing-llm"),
        pytest.param(None, "web_search_no_enabled", id="web-search-without-enabled"),
    ],
)
def test_save_manifest_request_rejects_missing_required_fields(
    make_save_manifest_request: Callable[..., SaveManifestRequest],
    drop_field: str | None,
    make_invalid: str | None,
) -> None:
    data = make_save_manifest_request().model_dump(mode="json")
    if drop_field is not None:
        del data[drop_field]
    if make_invalid == "web_search_no_enabled":
        data["web_search"] = {"provider": "brave"}
    with pytest.raises(ValidationError):
        SaveManifestRequest.model_validate(data)


def test_save_manifest_request_omits_web_search_when_absent(
    make_save_manifest_request: Callable[..., SaveManifestRequest],
) -> None:
    data = make_save_manifest_request().model_dump(mode="json")
    data.pop("web_search", None)
    parsed = SaveManifestRequest.model_validate(data)
    assert parsed.web_search is None
    assert "web_search" not in parsed.to_mapping()
    # Parser then applies the disabled-by-default.
    assert bundle_manifest_from_mapping(parsed.to_mapping()).web_search.enabled is False


def test_save_manifest_request_roundtrips_agents_and_channels(
    make_save_manifest_request: Callable[..., SaveManifestRequest],
) -> None:
    body = make_save_manifest_request()
    manifest = bundle_manifest_from_mapping(body.to_mapping())
    assert [s.id for s in manifest.agents.subagents] == ["scout", "supplier", "marketing"]
    assert manifest.channels.telegram.bot_token == "1234567890"
