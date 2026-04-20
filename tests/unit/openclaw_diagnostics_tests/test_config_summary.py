from __future__ import annotations

import json
from pathlib import Path

import pytest
from openclaw_diagnostics.config_summary import summarize_config

pytestmark = pytest.mark.unit


def test_summarize_config_channels_and_plugins(tmp_path: Path) -> None:
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(
        json.dumps(
            {
                "channels": {
                    "a": {"healthMonitor": {"enabled": True}},
                    "b": {},
                },
                "plugins": {
                    "allow": ["sellerclaw-ui"],
                    "entries": {"sellerclaw-ui": {}},
                    "load": {"paths": ["/opt/p"]},
                },
            }
        ),
        encoding="utf-8",
    )
    lines = summarize_config(cfg)
    assert any("channels=a, b" in line for line in lines)
    assert any("Channel a:" in line and "True" in line for line in lines)
    assert any("Plugin summary:" in line and "sellerclaw-ui" in line for line in lines)


def test_summarize_config_invalid_json(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.json"
    cfg.write_text("{not json", encoding="utf-8")
    lines = summarize_config(cfg)
    assert len(lines) == 1
    assert "Failed to parse config summary" in lines[0]


def test_summarize_config_empty_channels(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"channels": {}}), encoding="utf-8")
    lines = summarize_config(cfg)
    assert any("(none)" in line for line in lines)
