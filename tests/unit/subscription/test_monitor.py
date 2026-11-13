"""Reading the owner's Claude or ChatGPT plan: signed in? limit used up? when does it reset?"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sellerclaw_agent.subscription import chatgpt_auth
from sellerclaw_agent.subscription.claude_wrapper import limits_from_event
from sellerclaw_agent.subscription.monitor import (
    UNKNOWN_RESET_RETRY,
    AiSubscriptionMonitor,
    chatgpt_status_from,
    claude_status_from,
)
from sellerclaw_agent.subscription.state import save_choice

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
FIVE_HOURS_RESET = NOW + timedelta(hours=2)
WEEK_RESET = NOW + timedelta(days=3)


def _event(*, status: str, five_hour: str = "allowed", seven_day: str = "allowed") -> dict[str, Any]:
    """A ``rate_limit_event`` exactly as Claude Code 2.1.311 prints it in stream-json."""
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "resetsAt": int(WEEK_RESET.timestamp()),
            "rateLimitType": "seven_day",
            "utilization": 0.82,
            "unifiedWindows": {
                "five_hour": {"utilization": 0.19, "resetsAt": int(FIVE_HOURS_RESET.timestamp()), "status": five_hour},
                "seven_day": {"utilization": 0.82, "resetsAt": int(WEEK_RESET.timestamp()), "status": seven_day},
            },
        },
        "uuid": "83985965-2443-439c-8c76-de54f314dadb",
        "session_id": "123a0e10",
    }


def test_the_wrapper_keeps_both_windows_and_when_they_reset() -> None:
    limits = limits_from_event(_event(status="allowed_warning"), now=NOW)

    assert limits == {
        "observed_at": NOW.isoformat(),
        "status": "allowed_warning",
        "rate_limit_type": "seven_day",
        "resets_at": WEEK_RESET.isoformat(),
        "windows": {
            "five_hour": {"utilization": 0.19, "resets_at": FIVE_HOURS_RESET.isoformat(), "status": "allowed"},
            "seven_day": {"utilization": 0.82, "resets_at": WEEK_RESET.isoformat(), "status": "allowed"},
        },
    }


@pytest.mark.parametrize(
    "event",
    [
        pytest.param({"type": "assistant", "message": {}}, id="other-event"),
        pytest.param({"type": "rate_limit_event"}, id="no-info"),
    ],
)
def test_the_wrapper_ignores_everything_else(event: dict[str, Any]) -> None:
    assert limits_from_event(event, now=NOW) is None


def test_a_working_plan_reports_its_usage_as_percentages_and_when_the_plan_said_so() -> None:
    told_at = NOW - timedelta(minutes=40)  # Claude reports its limits only while the agent works
    status = claude_status_from(
        logged_in=True,
        plan="max",
        limits=limits_from_event(_event(status="allowed_warning"), now=told_at),
        now=NOW,
        main_model="claude-opus-5-5",
        specialist_model="claude-sonnet-5-5",
    )

    assert status.to_ping_payload() == {
        "provider": "claude",
        "state": "connected",
        "observed_at": NOW.isoformat(),
        "plan": "max",
        "main_model": "claude-opus-5-5",
        "specialist_model": "claude-sonnet-5-5",
        "windows": [
            {"window": "5h", "used_percent": 19, "reset_at": FIVE_HOURS_RESET.isoformat()},
            {"window": "week", "used_percent": 82, "reset_at": WEEK_RESET.isoformat()},
        ],
        "resumes_at": None,
        "limits_observed_at": told_at.isoformat(),
    }


def test_no_reading_yet_reports_no_usage_and_no_reading_time() -> None:
    status = claude_status_from(
        logged_in=True, plan="pro", limits=None, now=NOW, main_model=None, specialist_model=None
    )

    payload = status.to_ping_payload()
    assert (payload["windows"], payload["limits_observed_at"], payload["state"]) == ([], None, "connected")


@pytest.mark.parametrize(
    ("event", "resumes_at"),
    [
        pytest.param(_event(status="rejected", five_hour="rejected"), WEEK_RESET, id="overall-rejected"),
        pytest.param(_event(status="allowed", five_hour="rejected"), FIVE_HOURS_RESET, id="five-hour-window"),
    ],
)
def test_a_used_up_plan_waits_until_the_latest_rejected_window_resets(
    event: dict[str, Any], resumes_at: datetime
) -> None:
    status = claude_status_from(
        logged_in=True,
        plan="pro",
        limits=limits_from_event(event, now=NOW),
        now=NOW,
        main_model=None,
        specialist_model=None,
    )

    assert status.state == "limit_reached"
    assert status.resumes_at == resumes_at
    assert status.to_status_file() == {
        "provider": "claude",
        "state": "limit_reached",
        "observed_at": NOW.isoformat(),
        "resumes_at": resumes_at.isoformat(),
    }


def test_a_rejection_without_a_reset_time_tries_again_later() -> None:
    limits = {"observed_at": NOW.isoformat(), "status": "rejected", "resets_at": None, "windows": {}}

    status = claude_status_from(
        logged_in=True, plan=None, limits=limits, now=NOW, main_model=None, specialist_model=None
    )

    assert status.state == "limit_reached"
    assert status.resumes_at == NOW + UNKNOWN_RESET_RETRY


def test_a_limit_that_already_reset_no_longer_holds_the_agent() -> None:
    old = limits_from_event(_event(status="rejected", five_hour="rejected"), now=NOW - timedelta(days=4))

    status = claude_status_from(
        logged_in=True, plan="max", limits=old, now=NOW + timedelta(days=4), main_model=None, specialist_model=None
    )

    assert status.state == "connected"
    assert status.resumes_at is None


def test_a_lost_sign_in_wins_over_everything_else() -> None:
    status = claude_status_from(
        logged_in=False,
        plan=None,
        limits=limits_from_event(_event(status="rejected"), now=NOW),
        now=NOW,
        main_model=None,
        specialist_model=None,
    )

    assert status.state == "signed_out"
    assert status.resumes_at is None


# --- ChatGPT --------------------------------------------------------------------------------------

_MS = 1000


def _usage(*windows: dict[str, Any], plan: str | None = "pro") -> dict[str, Any]:
    """OpenClaw's ``usage.status`` answer with one ChatGPT provider entry."""
    entry: dict[str, Any] = {"provider": "openai", "displayName": "ChatGPT", "windows": list(windows)}
    if plan is not None:
        entry["plan"] = plan
    return {
        "updatedAt": int((NOW - timedelta(minutes=1)).timestamp() * _MS),
        "providers": [{"provider": "anthropic", "windows": []}, entry],
    }


def _window(label: str, used: float, reset: datetime | str | None) -> dict[str, Any]:
    raw: Any = int(reset.timestamp() * _MS) if isinstance(reset, datetime) else reset
    return {"label": label, "usedPercent": used, "resetAt": raw}


def test_a_working_chatgpt_plan_reports_both_windows_and_its_tier() -> None:
    status = chatgpt_status_from(
        signed_in=True,
        usage=_usage(_window("5h", 12.4, FIVE_HOURS_RESET), _window("Week", 63, WEEK_RESET)),
        now=NOW,
        main_model="gpt-6.1-sol",
        specialist_model="gpt-6.1-terra",
    )

    assert status.to_ping_payload() == {
        "provider": "chatgpt",
        "state": "connected",
        "observed_at": NOW.isoformat(),
        "plan": "pro",
        "main_model": "gpt-6.1-sol",
        "specialist_model": "gpt-6.1-terra",
        "windows": [
            {"window": "5h", "used_percent": 12, "reset_at": FIVE_HOURS_RESET.isoformat()},
            {"window": "week", "used_percent": 63, "reset_at": WEEK_RESET.isoformat()},
        ],
        "resumes_at": None,
        "limits_observed_at": (NOW - timedelta(minutes=1)).isoformat(),
    }


@pytest.mark.parametrize(
    ("windows", "resumes_at"),
    [
        pytest.param([_window("5h", 100, FIVE_HOURS_RESET)], FIVE_HOURS_RESET, id="five-hours-used-up"),
        pytest.param(
            [_window("5h", 100, FIVE_HOURS_RESET), _window("Week", 100, WEEK_RESET)], WEEK_RESET, id="both-wait-for-last"
        ),
        pytest.param([_window("Week", 100, WEEK_RESET.isoformat())], WEEK_RESET, id="iso-reset-time"),
    ],
)
def test_a_used_up_chatgpt_window_holds_the_agent_until_it_resets(
    windows: list[dict[str, Any]], resumes_at: datetime
) -> None:
    status = chatgpt_status_from(signed_in=True, usage=_usage(*windows), now=NOW, main_model=None, specialist_model=None)

    assert (status.state, status.resumes_at) == ("limit_reached", resumes_at)


def test_a_window_that_already_reset_no_longer_holds_the_agent() -> None:
    status = chatgpt_status_from(
        signed_in=True,
        usage=_usage(_window("5h", 100, NOW - timedelta(minutes=1))),
        now=NOW,
        main_model=None,
        specialist_model=None,
    )

    assert (status.state, status.resumes_at) == ("connected", None)


@pytest.mark.parametrize(
    ("signed_in", "usage", "state"),
    [
        pytest.param(False, _usage(_window("5h", 100, FIVE_HOURS_RESET)), "signed_out", id="lost-sign-in-wins"),
        pytest.param(True, None, "connected", id="no-reading-yet"),
        pytest.param(True, {"providers": [{"provider": "anthropic", "windows": []}]}, "connected", id="no-chatgpt-entry"),
    ],
)
def test_chatgpt_without_a_full_window(signed_in: bool, usage: dict[str, Any] | None, state: str) -> None:
    status = chatgpt_status_from(signed_in=signed_in, usage=usage, now=NOW, main_model=None, specialist_model=None)

    assert status.state == state
    if usage is None or state == "connected":
        assert (status.windows, status.limits_observed_at) == ((), None)


def _chatgpt_monitor(
    tmp_path: Path, make_openclaw_state: Any, profile: dict[str, Any] | None, answers: list[dict[str, Any] | None]
) -> AiSubscriptionMonitor:
    profiles = {"openai:default": profile} if profile is not None else {}
    state = make_openclaw_state(shared={"version": 1, "profiles": profiles})
    data_dir = tmp_path / "data"
    save_choice(data_dir, "chatgpt")
    replies = iter(answers)

    async def read_usage() -> dict[str, Any] | None:
        return next(replies)

    return AiSubscriptionMonitor(data_dir=data_dir, clock=lambda: NOW, state_dir=state.root, usage_reader=read_usage)


async def test_the_monitor_keeps_the_sign_in_copy_current(
    tmp_path: Path, make_openclaw_state: Any, make_chatgpt_profile: Any
) -> None:
    """An update restores what the copy holds, so every pass follows OpenClaw's refreshes."""
    monitor = _chatgpt_monitor(tmp_path, make_openclaw_state, make_chatgpt_profile(refresh="ref-9"), [_usage()])

    status = await monitor.observe()

    assert status is not None and status.state == "connected"
    assert chatgpt_auth.load_snapshot(monitor.data_dir).profiles == {
        "openai:default": make_chatgpt_profile(refresh="ref-9")
    }


async def test_a_missed_gateway_reading_keeps_a_held_agent_waiting(
    tmp_path: Path, make_openclaw_state: Any, make_chatgpt_profile: Any
) -> None:
    monitor = _chatgpt_monitor(
        tmp_path,
        make_openclaw_state,
        make_chatgpt_profile(),
        [_usage(_window("5h", 100, FIVE_HOURS_RESET)), None],
    )
    await monitor.refresh()

    second = await monitor.observe()

    assert second is not None
    assert (second.state, second.resumes_at, second.plan) == ("limit_reached", FIVE_HOURS_RESET, "pro")
    assert [w["used_percent"] for w in second.windows] == [100]


async def test_chatgpt_signed_out_when_openclaw_holds_no_sign_in_and_none_is_kept(
    tmp_path: Path, make_openclaw_state: Any
) -> None:
    """Nothing to put back (no copy on the data volume): the owner hears it at once."""
    monitor = _chatgpt_monitor(tmp_path, make_openclaw_state, None, [_usage()])

    status = await monitor.observe()

    assert status is not None and status.state == "signed_out"


def _replaced_container(tmp_path: Path, make_openclaw_state: Any, make_chatgpt_profile: Any, answers: list[Any]) -> AiSubscriptionMonitor:
    """``sellerclaw-agent update``: the copy waits on the data volume, OpenClaw's databases are new."""
    monitor = _chatgpt_monitor(tmp_path, make_openclaw_state, None, answers)
    chatgpt_auth.atomic_write_json(
        chatgpt_auth.snapshot_path(monitor.data_dir),
        {"version": 1, "profiles": {"openai:default": make_chatgpt_profile()}},
    )
    return monitor


async def test_a_replaced_container_is_not_reported_signed_out_before_the_gateway_starts(
    tmp_path: Path, make_openclaw_state: Any, make_chatgpt_profile: Any
) -> None:
    """The agent server polls before the gateway's start puts the copy back (``settle``); a
    stopped agent never starts the gateway at all. Neither is a lost sign-in."""
    monitor = _replaced_container(tmp_path, make_openclaw_state, make_chatgpt_profile, [None, None, None])

    states = [(await monitor.observe()).state for _ in range(3)]  # type: ignore[union-attr]

    assert states == ["connected", "connected", "connected"]


async def test_a_sign_in_gone_while_openclaw_runs_is_reported_on_the_second_miss(
    tmp_path: Path, make_openclaw_state: Any, make_chatgpt_profile: Any
) -> None:
    """One miss may be a token mid-refresh or a busy database; two in a row are a lost sign-in."""
    monitor = _replaced_container(tmp_path, make_openclaw_state, make_chatgpt_profile, [_usage(), _usage()])

    first = await monitor.observe()
    second = await monitor.observe()

    assert (first.state, second.state) == ("connected", "signed_out")  # type: ignore[union-attr]


async def test_an_answer_without_chatgpt_keeps_a_held_agent_waiting(
    tmp_path: Path, make_openclaw_state: Any, make_chatgpt_profile: Any
) -> None:
    monitor = _chatgpt_monitor(
        tmp_path,
        make_openclaw_state,
        make_chatgpt_profile(),
        [_usage(_window("5h", 100, FIVE_HOURS_RESET)), {"providers": [{"provider": "anthropic", "windows": []}]}],
    )
    await monitor.refresh()

    second = await monitor.observe()

    assert second is not None
    assert (second.state, second.resumes_at) == ("limit_reached", FIVE_HOURS_RESET)


def test_a_window_just_short_of_full_does_not_hold_the_agent() -> None:
    """99.5 % rounds to 100 — and a held weekly window would stop the agent for days."""
    status = chatgpt_status_from(
        signed_in=True, usage=_usage(_window("Week", 99.5, WEEK_RESET)), now=NOW, main_model=None, specialist_model=None
    )

    assert (status.state, [w["used_percent"] for w in status.windows]) == ("connected", [99])


def test_the_older_chatgpt_provider_id_counts_too() -> None:
    usage = _usage(_window("5h", 100, FIVE_HOURS_RESET))
    usage["providers"][1]["provider"] = "openai-codex"

    status = chatgpt_status_from(signed_in=True, usage=usage, now=NOW, main_model=None, specialist_model=None)

    assert (status.state, status.resumes_at) == ("limit_reached", FIVE_HOURS_RESET)