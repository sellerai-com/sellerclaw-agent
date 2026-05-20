from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from sellerclaw_agent.server.schemas import SaveManifestRequest
from sellerclaw_agent.test_manifest_fixtures import load_manifest_v2_mapping


@pytest.fixture()
def make_manifest_data() -> Callable[..., dict[str, Any]]:
    """Factory for the generic v2 dict accepted by ``POST /manifest``."""

    def _factory(**overrides: Any) -> dict[str, Any]:
        data = copy.deepcopy(load_manifest_v2_mapping())
        # Tests assert on a stable user_id; pin it to the canonical unit-test UUID.
        data["user_id"] = "11111111-1111-4111-8111-111111111111"
        data.update(overrides)
        return data

    return _factory


@pytest.fixture()
def make_save_manifest_request() -> Callable[..., SaveManifestRequest]:
    """Factory for ``SaveManifestRequest`` Pydantic objects (generic v2 shape)."""

    def _factory(**overrides: Any) -> SaveManifestRequest:
        data = copy.deepcopy(load_manifest_v2_mapping())
        data["user_id"] = "11111111-1111-4111-8111-111111111111"
        data.update(overrides)
        return SaveManifestRequest.model_validate(data)

    return _factory
