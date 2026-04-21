from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from sellerclaw_agent.bundle.manifest import (
    BundleManifest,
    TelegramManifest,
    WebSearchManifest,
    bundle_manifest_from_mapping,
)
from sellerclaw_agent.models import AgentModuleId, IntegrationKind

pytestmark = pytest.mark.unit


def _minimal_valid_bundle_mapping() -> dict[str, object]:
    return {
        "user_id": "11111111-1111-4111-8111-111111111111",
        "gateway_token": "g",
        "hooks_token": "h",
        "litellm_base_url": "http://litellm",
        "litellm_api_key": "k",
        "models": {
            "complex": {
                "id": "c",
                "name": "C",
                "reasoning": True,
                "input": ["text"],
                "context_window": 100,
                "max_tokens": 50,
            },
            "simple": {
                "id": "s",
                "name": "S",
                "reasoning": False,
                "input": ["text"],
                "context_window": 100,
                "max_tokens": 50,
            },
        },
        "template_variables": {},
    }


def test_bundle_manifest_to_save_manifest_mapping_roundtrip(make_manifest) -> None:
    manifest = make_manifest()
    mapping = manifest.to_save_manifest_mapping()
    again = bundle_manifest_from_mapping(mapping)
    assert again.user_id == manifest.user_id
    assert again.gateway_token == manifest.gateway_token
    assert again.model_complex.id == manifest.model_complex.id
    assert again.connected_integrations == manifest.connected_integrations
    assert again.proxy_url == manifest.proxy_url


def test_bundle_manifest_roundtrip_preserves_model_prefix() -> None:
    mapping = _minimal_valid_bundle_mapping()
    mapping["model_name_prefix"] = "u:abc/"
    loaded = bundle_manifest_from_mapping(mapping)
    assert loaded.model_name_prefix == "u:abc/"
    again = loaded.to_save_manifest_mapping()
    assert again["model_name_prefix"] == "u:abc/"


def test_bundle_manifest_roundtrip_preserves_proxy_url() -> None:
    mapping = _minimal_valid_bundle_mapping()
    mapping["proxy_url"] = "http://user:pass@proxy.example:3128"
    loaded = bundle_manifest_from_mapping(mapping)
    assert loaded.proxy_url == "http://user:pass@proxy.example:3128"
    assert loaded.to_save_manifest_mapping()["proxy_url"] == loaded.proxy_url


def test_bundle_manifest_yaml_roundtrip(tmp_path: Path, make_manifest) -> None:
    manifest = make_manifest()
    data = {
        "user_id": str(manifest.user_id),
        "gateway_token": manifest.gateway_token,
        "hooks_token": manifest.hooks_token,
        "litellm_base_url": manifest.litellm_base_url,
        "litellm_api_key": manifest.litellm_api_key,
        "models": {
            "complex": {
                "id": manifest.model_complex.id,
                "name": manifest.model_complex.name,
                "reasoning": manifest.model_complex.reasoning,
                "input": list(manifest.model_complex.input),
                "context_window": manifest.model_complex.context_window,
                "max_tokens": manifest.model_complex.max_tokens,
            },
            "simple": {
                "id": manifest.model_simple.id,
                "name": manifest.model_simple.name,
                "reasoning": manifest.model_simple.reasoning,
                "input": list(manifest.model_simple.input),
                "context_window": manifest.model_simple.context_window,
                "max_tokens": manifest.model_simple.max_tokens,
            },
        },
        "enabled_modules": [],
        "connected_integrations": [],
        "template_variables": manifest.template_variables,
    }
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    loaded = BundleManifest.from_yaml_file(path, expand_env=False)
    assert loaded.user_id == manifest.user_id
    assert loaded.model_complex.id == manifest.model_complex.id


def test_bundle_manifest_from_yaml_expands_env_vars(tmp_path: Path, make_manifest) -> None:
    manifest = make_manifest(litellm_api_key="SHOULD_NOT_APPEAR")
    data = {
        "user_id": str(manifest.user_id),
        "gateway_token": manifest.gateway_token,
        "hooks_token": manifest.hooks_token,
        "litellm_base_url": manifest.litellm_base_url,
        "litellm_api_key": "${BUNDLE_TEST_LITELLM_KEY}",
        "models": {
            "complex": {
                "id": manifest.model_complex.id,
                "name": manifest.model_complex.name,
                "reasoning": manifest.model_complex.reasoning,
                "input": list(manifest.model_complex.input),
                "context_window": manifest.model_complex.context_window,
                "max_tokens": manifest.model_complex.max_tokens,
            },
            "simple": {
                "id": manifest.model_simple.id,
                "name": manifest.model_simple.name,
                "reasoning": manifest.model_simple.reasoning,
                "input": list(manifest.model_simple.input),
                "context_window": manifest.model_simple.context_window,
                "max_tokens": manifest.model_simple.max_tokens,
            },
        },
        "enabled_modules": [],
        "connected_integrations": [],
        "template_variables": manifest.template_variables,
    }
    path = tmp_path / "env.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    os.environ["BUNDLE_TEST_LITELLM_KEY"] = "from-env-xyz"
    try:
        loaded = BundleManifest.from_yaml_file(path, expand_env=True)
        assert loaded.litellm_api_key == "from-env-xyz"
    finally:
        os.environ.pop("BUNDLE_TEST_LITELLM_KEY", None)


@pytest.mark.parametrize(
    ("bad_data", "match"),
    [
        pytest.param({}, "models", id="missing-root-keys"),
        pytest.param({"user_id": "x", "models": {}}, "models.complex", id="empty-models"),
        pytest.param(
            {**_minimal_valid_bundle_mapping(), "telegram": "not-a-mapping"},
            "telegram must be a mapping",
            id="telegram-not-mapping",
        ),
        pytest.param(
            {**_minimal_valid_bundle_mapping(), "enabled_modules": "x"},
            "enabled_modules must be a list",
            id="enabled-modules-not-list",
        ),
        pytest.param(
            {**_minimal_valid_bundle_mapping(), "user_id": "not-a-uuid"},
            "badly formed hexadecimal UUID string",
            id="invalid-user-id",
        ),
    ],
)
def test_bundle_manifest_from_mapping_validation_errors(bad_data: dict, match: str) -> None:
    with pytest.raises((KeyError, ValueError, TypeError), match=match):
        bundle_manifest_from_mapping(bad_data)  # type: ignore[arg-type]


def test_bundle_manifest_minimal_yaml_defaults_nested_sections(tmp_path: Path, make_manifest) -> None:
    manifest = make_manifest()
    data = {
        "user_id": str(manifest.user_id),
        "gateway_token": "g",
        "hooks_token": "h",
        "litellm_base_url": manifest.litellm_base_url,
        "litellm_api_key": "k",
        "models": {
            "complex": {
                "id": "c",
                "name": "C",
                "reasoning": True,
                "input": ["text"],
                "context_window": 1,
                "max_tokens": 2,
            },
            "simple": {
                "id": "s",
                "name": "S",
                "reasoning": False,
                "input": ["text"],
                "context_window": 1,
                "max_tokens": 2,
            },
        },
        "enabled_modules": [],
        "connected_integrations": [],
        "template_variables": manifest.template_variables,
    }
    path = tmp_path / "minimal.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    loaded = BundleManifest.from_yaml_file(path, expand_env=False)
    assert loaded.telegram == TelegramManifest()
    assert loaded.web_search == WebSearchManifest()


def test_bundle_manifest_resolved_modules_and_browser(make_manifest) -> None:
    m = make_manifest(
        enabled_module_ids=("shopify_store_manager", "ebay_store_manager"),
        per_module_browser={"shopify_store_manager": False, "ebay_store_manager": True},
    )
    resolved = m.resolved_enabled_modules()
    assert resolved == [AgentModuleId.SHOPIFY_STORE_MANAGER, AgentModuleId.EBAY_STORE_MANAGER]
    pmb = m.resolved_per_module_browser()
    assert pmb[AgentModuleId.SHOPIFY_STORE_MANAGER] is False
    assert pmb[AgentModuleId.EBAY_STORE_MANAGER] is True


def test_bundle_manifest_connected_integrations_from_yaml(tmp_path: Path, make_manifest) -> None:
    manifest = make_manifest()
    data = {
        "user_id": str(manifest.user_id),
        "gateway_token": manifest.gateway_token,
        "hooks_token": manifest.hooks_token,
        "litellm_base_url": manifest.litellm_base_url,
        "litellm_api_key": manifest.litellm_api_key,
        "models": {
            "complex": {
                "id": "c",
                "name": "C",
                "reasoning": True,
                "input": ["text"],
                "context_window": 1,
                "max_tokens": 2,
            },
            "simple": {
                "id": "s",
                "name": "S",
                "reasoning": False,
                "input": ["text"],
                "context_window": 1,
                "max_tokens": 2,
            },
        },
        "enabled_modules": [],
        "connected_integrations": ["shopify_store", "ebay_store"],
        "template_variables": manifest.template_variables,
    }
    path = tmp_path / "conn.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    loaded = BundleManifest.from_yaml_file(path, expand_env=False)
    assert loaded.connected_integrations == frozenset(
        {IntegrationKind.SHOPIFY_STORE, IntegrationKind.EBAY_STORE}
    )
