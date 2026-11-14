"""The "AI:" block of ``sellerclaw-agent status``: which plan powers the agent and how much is left."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sellerclaw_agent import cli, cli_subscription

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def utc_machine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(autouse=True)
def installed_with_one_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hints name ``sellerclaw-agent``, as the one-command install (``./setup.sh`` in a checkout)."""
    monkeypatch.setattr(cli, "_installed_from_repo", lambda: False)


def _agent_reports(monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]) -> None:
    monkeypatch.setattr(cli_subscription, "_call", lambda method, path, **_: state)


def test_a_weekly_reset_a_week_away_names_its_date_not_just_the_weekday(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a Friday, "resets Fri 00:00" would read as tonight; the reset a week away says which Friday."""
    now = datetime.now(UTC)
    five_hour_reset = (now + timedelta(hours=2)).replace(second=0, microsecond=0)
    weekly_reset = (now + timedelta(days=6, hours=13)).replace(second=0, microsecond=0)
    _agent_reports(
        monkeypatch,
        {
            "provider": "claude",
            "offered": True,
            "status": {
                "state": "connected",
                "plan": "max",
                "windows": [
                    {"window": "5h", "used_percent": 42, "reset_at": five_hour_reset.isoformat()},
                    {"window": "week", "used_percent": 5, "reset_at": weekly_reset.isoformat()},
                ],
            },
        },
    )

    assert cli_subscription.ai_status_lines() == [
        "AI: your Claude plan (Max)",
        f"    5-hour limit: 42% used, resets {five_hour_reset.strftime('%a %H:%M')}",
        f"    weekly limit: 5% used, resets {weekly_reset.strftime('%a %d %b %H:%M')}",
    ]


def _in(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(second=0, microsecond=0).isoformat()


_RESUMES = _in(3)


@pytest.mark.parametrize(
    ("state", "row"),
    [
        pytest.param(
            {"provider": None, "offered": False, "status": None},
            ("SellerClaw credits (to use your ChatGPT or Claude plan: sellerclaw-agent connect chatgpt | claude)", ""),
            id="credits",
        ),
        pytest.param(
            {
                "provider": "claude",
                "offered": True,
                "status": {
                    "state": "connected",
                    "plan": "max",
                    "windows": [
                        {"window": "5h", "used_percent": 47, "reset_at": _in(2)},
                        {"window": "week", "used_percent": 6, "reset_at": _in(100)},
                    ],
                },
            },
            ("your Claude plan (Max), limits used: 5-hour 47%, weekly 6%", "green"),
            id="own-plan-with-limits",
        ),
        pytest.param(
            {"provider": "chatgpt", "offered": True, "status": None},
            ("your ChatGPT plan", "green"),
            id="own-plan-before-the-first-reading",
        ),
        pytest.param(
            {"provider": "chatgpt", "offered": False, "status": {"state": "connected", "plan": "plus"}},
            (
                "your ChatGPT plan (Plus) — not in use: your agent runs in the cloud; move it to your computer in Hosting",
                "yellow",
            ),
            id="hosted-in-the-cloud",
        ),
        pytest.param(
            {"provider": "chatgpt", "offered": True, "status": {"state": "limit_reached", "resumes_at": _RESUMES}},
            (
                "your ChatGPT plan — usage limit used up, waiting until "
                + datetime.fromisoformat(_RESUMES).strftime("%a %H:%M"),  # the test machine runs on UTC
                "yellow",
            ),
            id="limit-reached",
        ),
        pytest.param(
            {"provider": "claude", "offered": True, "status": {"state": "signed_out", "plan": "pro"}},
            ("your Claude plan (Pro) — signed out, run: sellerclaw-agent connect claude", "bold red"),
            id="signed-out",
        ),
        pytest.param(None, None, id="agent-cannot-say"),
    ],
)
def test_the_status_panel_row_says_what_powers_the_agent(
    state: dict[str, Any] | None, row: tuple[str, str] | None
) -> None:
    assert cli_subscription.ai_panel_row(state) == row


def test_a_checkout_hints_the_setup_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_installed_from_repo", lambda: True)

    row = cli_subscription.ai_panel_row({"provider": None})

    assert row == ("SellerClaw credits (to use your ChatGPT or Claude plan: ./setup.sh connect chatgpt | claude)", "")
