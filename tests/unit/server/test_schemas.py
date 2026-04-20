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
    assert manifest.gateway_token == "g"
    assert manifest.user_id == UUID("11111111-1111-4111-8111-111111111111")


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


def test_to_mapping_rejected_by_bundle_for_unknown_integration(
    make_save_manifest_request: Callable[..., SaveManifestRequest],
) -> None:
    body = make_save_manifest_request()
    mapping = body.to_mapping()
    mapping = {**mapping, "connected_integrations": ["unknown_integration_xyz"]}
    with pytest.raises(ValueError, match="unknown_integration_xyz"):
        bundle_manifest_from_mapping(mapping)
