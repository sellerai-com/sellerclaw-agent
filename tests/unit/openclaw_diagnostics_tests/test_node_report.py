from __future__ import annotations

import json
from pathlib import Path

import pytest
from openclaw_diagnostics.node_report import summarize_reports

pytestmark = pytest.mark.unit


def test_summarize_reports_empty_dir(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    lines = summarize_reports(d)
    assert len(lines) == 1
    assert "No Node.js diagnostic reports found" in lines[0]


def test_summarize_reports_parse_error(tmp_path: Path) -> None:
    d = tmp_path / "diag"
    d.mkdir()
    (d / "bad.json").write_text("{", encoding="utf-8")
    lines = summarize_reports(d)
    assert any("Report parse error" in line for line in lines)


def test_summarize_reports_valid(tmp_path: Path) -> None:
    d = tmp_path / "diag"
    d.mkdir()
    payload = {
        "header": {
            "trigger": "FatalError",
            "event": "OOMError",
            "nodejsVersion": "v20.0.0",
        },
        "resourceUsage": {"rss": 10485760},
        "javascriptHeap": {
            "totalMemory": 1048576,
            "usedMemory": 1048576,
            "memoryLimit": 1073741824,
            "spaces": [
                {
                    "spaceName": "old_space",
                    "spaceUsedSize": 12 * 1024 * 1024,
                    "spaceAvailableSize": 0,
                    "physicalSpaceSize": 16 * 1024 * 1024,
                }
            ],
        },
        "javascriptStack": {"message": "OOM", "stack": ["frame1"]},
    }
    (d / "report.json").write_text(json.dumps(payload), encoding="utf-8")
    lines = summarize_reports(d)
    assert any("Node report:" in line and "OOMError" in line for line in lines)
    assert any("heap space: old_space" in line for line in lines)
    assert any("JS error: OOM" in line for line in lines)
    assert any("at frame1" in line for line in lines)
