"""Packaged agent-manifest-schema.json (v2) must validate SaveManifestRequest.to_mapping()."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from sellerclaw_agent.server.schemas import SaveManifestRequest
from sellerclaw_agent.test_manifest_fixtures import load_manifest_v2_mapping

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
    req = SaveManifestRequest.model_validate(load_manifest_v2_mapping())
    jsonschema.validate(instance=req.to_mapping(), schema=agent_manifest_schema)


def test_agent_manifest_schema_allows_extra_web_search_properties(
    agent_manifest_schema: dict[str, object],
) -> None:
    """Raw JSON may still include legacy web_search keys during monolith migration."""
    instance = copy.deepcopy(load_manifest_v2_mapping())
    instance["web_search"] = {
        "enabled": True,
        "provider": "brave",
        "api_key": "legacy-key",
        "base_url": "https://old.example",
    }
    jsonschema.validate(instance=instance, schema=agent_manifest_schema)


def test_agent_manifest_schema_rejects_missing_agents(
    agent_manifest_schema: dict[str, object],
) -> None:
    instance = copy.deepcopy(load_manifest_v2_mapping())
    del instance["agents"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=instance, schema=agent_manifest_schema)
