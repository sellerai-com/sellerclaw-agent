"""Monorepo copy of agent-manifest-schema.json must validate SaveManifestRequest.to_mapping()."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from sellerclaw_agent.server.schemas import SaveManifestRequest

pytestmark = pytest.mark.unit

_AGENT_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = _AGENT_ROOT / "docs" / "contracts" / "agent-manifest-schema.json"


@pytest.fixture(scope="module")
def agent_manifest_schema() -> dict[str, object]:
    assert _SCHEMA_PATH.is_file(), f"missing schema copy: {_SCHEMA_PATH}"
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def test_save_manifest_request_mapping_validates_against_packaged_schema(
    agent_manifest_schema: dict[str, object],
) -> None:
    req = SaveManifestRequest.model_validate(
        {
            "user_id": "11111111-1111-4111-8111-111111111111",
            "gateway_token": "g",
            "hooks_token": "h",
            "litellm_base_url": "http://litellm",
            "litellm_api_key": "k",
            "models": {
                "complex": {
                    "id": "c1",
                    "name": "C",
                    "reasoning": True,
                    "input": ["text"],
                    "context_window": 100,
                    "max_tokens": 50,
                },
                "simple": {
                    "id": "s1",
                    "name": "S",
                    "reasoning": False,
                    "input": ["text"],
                    "context_window": 100,
                    "max_tokens": 50,
                },
            },
            "template_variables": {"api_base_path": "/agent"},
            "enabled_modules": ["product_scout"],
            "connected_integrations": ["research_trends"],
            "model_name_prefix": "u:abc/",
        }
    )
    jsonschema.validate(instance=req.to_mapping(), schema=agent_manifest_schema)
