from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from openclaw_diagnostics.process_monitor import (
    collect_child_tree,
    emit_process_snapshot,
    monitor_memory,
    parse_kv,
)

pytestmark = pytest.mark.unit


def test_parse_kv() -> None:
    text = "Name:\tbash\nVmRSS:\t1024 kB\n"
    assert parse_kv(text)["Name"] == "bash"
    assert parse_kv(text)["VmRSS"] == "1024 kB"


def test_collect_child_tree_linear(tmp_path: Path) -> None:
    proc = tmp_path
    (proc / "100" / "task" / "100").mkdir(parents=True)
    (proc / "100" / "task" / "100" / "children").write_text("200\n", encoding="utf-8")
    (proc / "200" / "task" / "200").mkdir(parents=True)
    (proc / "200" / "task" / "200" / "children").write_text("", encoding="utf-8")

    pids = collect_child_tree("100", proc_root=proc)
    assert "100" in pids
    assert "200" in pids


def test_emit_process_snapshot_basic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proc = tmp_path
    pid = "42"
    status_dir = proc / pid
    status_dir.mkdir(parents=True)
    (status_dir / "status").write_text(
        "VmRSS:\t100 kB\nVmHWM:\t200 kB\nVmSize:\t300 kB\nThreads:\t3\n",
        encoding="utf-8",
    )
    (status_dir / "fd").mkdir()
    (status_dir / "smaps_rollup").write_text("Pss:\t50 kB\nSwap:\t0 kB\n", encoding="utf-8")
    (status_dir / "task" / pid).mkdir(parents=True)
    (status_dir / "task" / pid / "children").write_text("", encoding="utf-8")

    emit_process_snapshot(pid, proc_root=proc)
    out = capsys.readouterr().out
    assert "Gateway process stats:" in out
    assert "pid=42" in out
    assert "rss=100 kB" in out


def test_monitor_memory_returns_without_emit_when_pid_missing() -> None:
    with (
        patch(
            "openclaw_diagnostics.process_monitor.os.kill",
            side_effect=ProcessLookupError(),
        ),
        patch("openclaw_diagnostics.process_monitor.emit_process_snapshot") as emit_mock,
        patch("openclaw_diagnostics.process_monitor.time.sleep") as sleep_mock,
    ):
        monitor_memory(4242, interval_seconds=1, proc_root=Path("/tmp"))
    emit_mock.assert_not_called()
    sleep_mock.assert_not_called()


def test_monitor_memory_one_snapshot_then_exits_when_pid_dies() -> None:
    with (
        patch(
            "openclaw_diagnostics.process_monitor.os.kill",
            side_effect=[None, ProcessLookupError()],
        ),
        patch("openclaw_diagnostics.process_monitor.emit_process_snapshot") as emit_mock,
        patch("openclaw_diagnostics.process_monitor.time.sleep") as sleep_mock,
    ):
        monitor_memory(7, interval_seconds=0, proc_root=Path("/proc"))
    emit_mock.assert_called_once_with("7", proc_root=Path("/proc"))
    sleep_mock.assert_called_once_with(0)


def test_monitor_memory_stops_after_max_samples() -> None:
    with (
        patch("openclaw_diagnostics.process_monitor.os.kill", return_value=None),
        patch("openclaw_diagnostics.process_monitor.emit_process_snapshot") as emit_mock,
        patch("openclaw_diagnostics.process_monitor.time.sleep") as sleep_mock,
    ):
        monitor_memory(7, interval_seconds=3, max_samples=2, proc_root=Path("/proc"))
    assert emit_mock.call_count == 2
    sleep_mock.assert_called_once_with(3)
