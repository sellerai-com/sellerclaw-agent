from __future__ import annotations

from pathlib import Path

import pytest
from openclaw_diagnostics.cgroup import (
    cgroup_limits_lines,
    cgroup_snapshot_raw_lines,
    fmt_bytes,
    read_val,
)

pytestmark = pytest.mark.unit


def test_fmt_bytes_none_and_max() -> None:
    assert fmt_bytes(None) == "n/a"
    assert fmt_bytes("max") == "max"


def test_fmt_bytes_numeric_mb() -> None:
    assert fmt_bytes("1048576") == "1MB"
    assert fmt_bytes("2097152") == "2MB"


def test_fmt_bytes_invalid_returns_raw() -> None:
    assert fmt_bytes("not-a-number") == "not-a-number"


def test_read_val_missing(tmp_path: Path) -> None:
    assert read_val(tmp_path / "missing") is None


def test_read_val_present(tmp_path: Path) -> None:
    p = tmp_path / "f"
    p.write_text("hello\n", encoding="utf-8")
    assert read_val(p) == "hello"


def test_cgroup_limits_lines_v2(tmp_path: Path) -> None:
    cg = tmp_path
    (cg / "memory.current").write_text("1048576\n", encoding="utf-8")
    (cg / "memory.max").write_text("max\n", encoding="utf-8")
    (cg / "memory.swap.current").write_text("0\n", encoding="utf-8")
    (cg / "memory.swap.max").write_text("0\n", encoding="utf-8")

    lines = cgroup_limits_lines(cgroup_sys=cg)
    assert len(lines) == 1
    assert "[openclaw_start] Cgroup memory at startup:" in lines[0]
    assert "current=1MB" in lines[0]
    assert "limit=max" in lines[0]


def test_cgroup_limits_lines_v1_fallback(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "memory.usage_in_bytes").write_text("2097152\n", encoding="utf-8")
    (mem / "memory.limit_in_bytes").write_text("10485760\n", encoding="utf-8")
    (mem / "memory.memsw.usage_in_bytes").write_text("0\n", encoding="utf-8")
    (mem / "memory.memsw.limit_in_bytes").write_text("10485760\n", encoding="utf-8")

    lines = cgroup_limits_lines(cgroup_sys=tmp_path)
    assert len(lines) == 1
    assert "current=2MB" in lines[0]


def test_cgroup_limits_lines_no_data(tmp_path: Path) -> None:
    lines = cgroup_limits_lines(cgroup_sys=tmp_path)
    assert lines == ["[openclaw_start] Cgroup memory info not available"]


def test_cgroup_snapshot_raw_lines(tmp_path: Path) -> None:
    (tmp_path / "memory.current").write_text("100\n", encoding="utf-8")
    line = cgroup_snapshot_raw_lines(cgroup_sys=tmp_path)
    assert line is not None
    assert "memory.current=100" in line


def test_cgroup_snapshot_raw_lines_empty(tmp_path: Path) -> None:
    assert cgroup_snapshot_raw_lines(cgroup_sys=tmp_path) is None
