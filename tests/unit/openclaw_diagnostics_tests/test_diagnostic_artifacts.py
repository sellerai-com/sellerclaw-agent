from __future__ import annotations

from pathlib import Path

import pytest
from openclaw_diagnostics.diagnostic_artifacts import list_diagnostic_artifact_lines

pytestmark = pytest.mark.unit


def test_list_artifacts_missing_dir(tmp_path: Path) -> None:
    lines = list_diagnostic_artifact_lines(tmp_path / "nope")
    assert len(lines) == 1
    assert "Node diagnostic directory missing" in lines[0]


def test_list_artifacts_empty_dir(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    lines = list_diagnostic_artifact_lines(d)
    assert len(lines) == 1
    assert "none in" in lines[0]


def test_list_artifacts_with_files(tmp_path: Path) -> None:
    d = tmp_path / "diag"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    lines = list_diagnostic_artifact_lines(d)
    assert len(lines) == 1
    assert "Node diagnostic artifact:" in lines[0]
    assert "size_bytes=1" in lines[0]
