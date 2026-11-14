"""The ping loop's last error and its code, as ``/health`` (and through it the CLI) reads them."""

from __future__ import annotations

import pytest
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        pytest.param("agent_runs_in_cloud", "agent_runs_in_cloud", id="named-refusal"),
        pytest.param(None, None, id="network-error"),
    ],
)
def test_mark_ping_error_records_the_code(code: str | None, expected: str | None) -> None:
    reg = EdgeRuntimeRegistry()

    reg.mark_ping_error("Your agent runs in SellerClaw's cloud.", code=code)

    ping = reg.snapshot_tasks()["ping_loop"]
    assert ping["last_error"] == "Your agent runs in SellerClaw's cloud."
    assert ping["last_error_code"] == expected
    assert ping["consecutive_errors"] == 1


def test_a_later_error_without_a_code_replaces_the_code() -> None:
    reg = EdgeRuntimeRegistry()
    reg.mark_ping_error("runs in cloud", code="agent_runs_in_cloud")

    reg.mark_ping_error("network down")

    assert reg.snapshot_tasks()["ping_loop"]["last_error_code"] is None


def test_a_successful_ping_clears_error_and_code() -> None:
    reg = EdgeRuntimeRegistry()
    reg.mark_ping_error("handing over", code="agent_move_in_progress")

    reg.mark_ping_success()

    ping = reg.snapshot_tasks()["ping_loop"]
    assert ping["last_error"] is None
    assert ping["last_error_code"] is None
    assert ping["consecutive_errors"] == 0
    assert ping["last_success_at"] is not None


def test_reset_forgets_the_previous_sign_ins_errors_but_not_its_last_success() -> None:
    reg = EdgeRuntimeRegistry()
    reg.mark_ping_success()
    reg.mark_ping_error("runs in cloud", code="agent_runs_in_cloud")

    reg.reset_ping_errors()

    ping = reg.snapshot_tasks()["ping_loop"]
    assert (ping["last_error"], ping["last_error_code"], ping["consecutive_errors"]) == (None, None, 0)
    assert ping["last_success_at"] is not None


def test_other_tasks_report_no_error_code() -> None:
    reg = EdgeRuntimeRegistry()
    reg.mark_command_finished(command_type="start", outcome="failed", error="boom")

    tasks = reg.snapshot_tasks()

    assert {name: t["last_error_code"] for name, t in tasks.items()} == {
        "ping_loop": None,
        "command_executor": None,
        "chat_sse": None,
        "hooks_sse": None,
    }
