from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_sellerclaw_ui_manifest_declares_channel_config_metadata() -> None:
    manifest_path = _REPO_ROOT / "plugins" / "sellerclaw-ui" / "openclaw.plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["kind"] == "channel"
    assert manifest["channels"] == ["sellerclaw-ui"]

    channel_config = manifest["channelConfigs"]["sellerclaw-ui"]
    schema = channel_config["schema"]
    properties = schema["properties"]

    assert channel_config["label"] == "SellerClaw UI"
    assert schema["type"] == "object"
    assert set(schema["required"]) == {
        "apiBaseUrl",
        "userId",
        "agentApiKey",
        "internalWebhookSecret",
    }
    assert set(properties) >= {
        "apiBaseUrl",
        "userId",
        "agentApiKey",
        "internalWebhookSecret",
        "primaryChannel",
        "localAgentBaseUrl",
    }
    assert channel_config["uiHints"]["agentApiKey"]["sensitive"] is True
    assert channel_config["uiHints"]["internalWebhookSecret"]["sensitive"] is True
