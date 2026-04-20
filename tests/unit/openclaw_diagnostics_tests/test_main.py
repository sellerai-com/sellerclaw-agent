from __future__ import annotations

from pathlib import Path

import pytest
from openclaw_diagnostics.__main__ import main

pytestmark = pytest.mark.unit


def test_main_validate_config_ok(tmp_path: Path) -> None:
    cfg = tmp_path / "ok.json"
    cfg.write_text(
        '{"gateway": {"mode": "local", "auth": {"mode": "token", "token": "scw_gateway_abc"}}}',
        encoding="utf-8",
    )
    code = main(["validate-config", str(cfg)])
    assert code == 0


def test_main_validate_config_fail(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.json"
    cfg.write_text("{}", encoding="utf-8")
    code = main(["validate-config", str(cfg)])
    assert code == 1


def test_main_config_summary(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text('{"channels": {}}', encoding="utf-8")
    code = main(["config-summary", str(cfg)])
    assert code == 0


def test_main_cgroup_limits() -> None:
    code = main(["cgroup-limits"])
    assert code == 0


def test_main_diagnostic_artifacts_missing(tmp_path: Path) -> None:
    code = main(["diagnostic-artifacts", str(tmp_path / "missing")])
    assert code == 0


def test_main_node_report_missing_dir(tmp_path: Path) -> None:
    code = main(["node-report", str(tmp_path / "missing")])
    assert code == 0
