from __future__ import annotations

import pytest
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit


def test_mark_command_finished_records_last_command() -> None:
    reg = EdgeRuntimeRegistry()
    assert reg.snapshot_last_command() is None

    reg.mark_command_finished(
        command_type="start",
        outcome="failed",
        error="Complex model gpt-5.4 is not available.",
    )
    snap = reg.snapshot_last_command()
    assert snap is not None
    assert snap["command_type"] == "start"
    assert snap["outcome"] == "failed"
    assert snap["error"] == "Complex model gpt-5.4 is not available."
    assert isinstance(snap["finished_at"], str) and snap["finished_at"]


def test_mark_command_finished_overwrites_previous() -> None:
    reg = EdgeRuntimeRegistry()
    reg.mark_command_finished(command_type="start", outcome="failed", error="boom")
    reg.mark_command_finished(command_type="restart", outcome="completed", error=None)
    snap = reg.snapshot_last_command()
    assert snap is not None
    assert snap["command_type"] == "restart"
    assert snap["outcome"] == "completed"
    assert snap["error"] is None


def test_mark_command_finished_failed_sets_executor_last_error() -> None:
    """Surfaces command failures so the status panel can show them as a
    fallback when there is no Last command row consumer.
    """
    reg = EdgeRuntimeRegistry()
    reg.mark_command_finished(command_type="start", outcome="failed", error="boom")
    tasks = reg.snapshot_tasks()
    assert "boom" in (tasks["command_executor"]["last_error"] or "")


def test_mark_command_finished_completed_clears_executor_last_error() -> None:
    reg = EdgeRuntimeRegistry()
    reg.mark_command_finished(command_type="start", outcome="failed", error="boom")
    reg.mark_command_finished(command_type="start", outcome="completed", error=None)
    tasks = reg.snapshot_tasks()
    assert tasks["command_executor"]["last_error"] is None
    assert tasks["command_executor"]["last_success_at"] is not None


def test_mark_command_finished_truncates_long_error() -> None:
    reg = EdgeRuntimeRegistry()
    huge = "x" * 5000
    reg.mark_command_finished(command_type="start", outcome="failed", error=huge)
    snap = reg.snapshot_last_command()
    assert snap is not None
    err = snap["error"]
    assert isinstance(err, str)
    assert len(err) <= 500
