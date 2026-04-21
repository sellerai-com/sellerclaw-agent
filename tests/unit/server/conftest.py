from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sellerclaw_agent.server.schemas import (
    ManifestModels,
    ManifestModelSpec,
    SaveManifestRequest,
)

_DEFAULT_MODEL = {
    "id": "c1",
    "name": "C",
    "reasoning": True,
    "input": ["text"],
    "context_window": 100,
    "max_tokens": 50,
}
_DEFAULT_SIMPLE_MODEL = {
    "id": "s1",
    "name": "S",
    "reasoning": False,
    "input": ["text"],
    "context_window": 100,
    "max_tokens": 50,
}


@pytest.fixture()
def make_manifest_data() -> Callable[..., dict[str, Any]]:
    """Factory for the raw dict accepted by ``POST /manifest``."""

    def _factory(**overrides: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "user_id": "11111111-1111-4111-8111-111111111111",
            "gateway_token": "g",
            "hooks_token": "h",
            "litellm_base_url": "http://litellm",
            "litellm_api_key": "k",
            "models": {
                "complex": dict(_DEFAULT_MODEL),
                "simple": dict(_DEFAULT_SIMPLE_MODEL),
            },
            "template_variables": {"api_base_path": "/agent"},
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
            "gateway_token": "g",
            "hooks_token": "h",
            "litellm_base_url": "http://litellm",
            "litellm_api_key": "k",
            "models": ManifestModels(
                complex=ManifestModelSpec(**_DEFAULT_MODEL),
                simple=ManifestModelSpec(**_DEFAULT_SIMPLE_MODEL),
            ),
            "template_variables": {"x": "y", "api_base_path": "/agent"},
            "enabled_modules": [],
            "connected_integrations": [],
        }
        defaults.update(overrides)
        return SaveManifestRequest(**defaults)

    return _factory
