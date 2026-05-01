from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sellerclaw_agent.server.schemas import SaveManifestRequest


@pytest.fixture()
def make_manifest_data() -> Callable[..., dict[str, Any]]:
    """Factory for the raw dict accepted by ``POST /manifest``."""

    def _factory(**overrides: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "user_id": "11111111-1111-4111-8111-111111111111",
            "litellm_base_url": "http://litellm",
            "litellm_api_key": "k",
            "template_variables": {},
            "agent_api_base_path": "/agent",
            "enabled_modules": [],
            "connected_integrations": [],
        }
        data.update(overrides)
        return data

    return _factory


@pytest.fixture()
def make_save_manifest_request() -> Callable[..., SaveManifestRequest]:
    """Factory for ``SaveManifestRequest`` Pydantic objects."""

    def _factory(**overrides: Any) -> SaveManifestRequest:
        defaults: dict[str, Any] = {
            "user_id": UUID("11111111-1111-4111-8111-111111111111"),
            "litellm_base_url": "http://litellm",
            "litellm_api_key": "k",
            "template_variables": {"x": "y"},
            "agent_api_base_path": "/agent",
            "enabled_modules": [],
            "connected_integrations": [],
        }
        defaults.update(overrides)
        return SaveManifestRequest(**defaults)

    return _factory
