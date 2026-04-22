"""Unit tests for live ``setup`` status watch (Rich panel + control loop)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from rich.console import Console, RenderableType
from rich.live import Live

from sellerclaw_agent.cli_watch import (
    FUTURE_SKEW_TOLERANCE_S,
    MAX_ERROR_CHARS,
    WATCH_STOPPED_HINT,
    _first_task_last_error,
    _parse_last_success_ago,
    render_status_panel,
    run_status_watch,
)

pytestmark = pytest.mark.unit


REF_TS = 1_700_000_000.0


def _plain_console(width: int = 100) -> Console:
    return Console(
        record=True,
        width=width,
        force_terminal=True,
        color_system="truecolor",
    )


def _render_to_text(renderable: RenderableType, *, width: int = 100) -> str:
    console = _plain_console(width=width)
    console.print(renderable)
    return console.export_text().lower()


def _iso(*, offset_s: float) -> str:
    return (
        datetime.fromtimestamp(REF_TS + offset_s, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _health_snapshot(  # noqa: PLR0913
    *,
    session_connected: bool | None = True,
    ping_last_success_at: str | None = None,
    chat_connected: bool | None = True,
    chat_last_error: str | None = None,
    ping_last_error: str | None = None,
    command_last_error: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "healthy",
        "edge_ping_enabled": True,
        "session": {"connected": session_connected, "agent_instance_id": "x"},
        "tasks": {
            "chat_sse": {"connected": chat_connected, "last_error": chat_last_error},
            "command_executor": {"last_error": command_last_error},
            "ping_loop": {
                "last_success_at": ping_last_success_at,
                "last_error": ping_last_error,
            },
        },
        "openclaw": {},
    }


# ---------------------------------------------------------------------------
# render_status_panel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snapshot", "must_contain", "must_not_contain"),
    [
        pytest.param(
            _health_snapshot(ping_last_success_at=_iso(offset_s=-5.0)),
            ["connected", "5s ago"],
            ["reconnecting", "null"],
            id="connected_fresh_ping",
        ),
        pytest.param(
            _health_snapshot(ping_last_success_at=_iso(offset_s=-100.0)),
            ["100s ago"],
            [],
            id="stale_yellow",
        ),
        pytest.param(
            _health_snapshot(ping_last_success_at=_iso(offset_s=-200.0)),
            ["200s ago"],
            [],
            id="stale_red",
        ),
        pytest.param(
            _health_snapshot(
                session_connected=False,
                chat_connected=False,
                ping_last_success_at=None,
            ),
            ["disconnected", "reconnecting", "null"],
            ["5s ago"],
            id="disconnected",
        ),
        pytest.param(
            {"status": "healthy", "session": {}, "tasks": {}},
            ["unknown", "null"],
            [],
            id="missing_fields",
        ),
    ],
)
def test_render_status_panel_core_states(
    snapshot: dict[str, Any] | None,
    must_contain: list[str],
    must_not_contain: list[str],
) -> None:
    text = _render_to_text(render_status_panel(snapshot, now=REF_TS))
    for token in must_contain:
        assert token.lower() in text, text
    for token in must_not_contain:
        assert token.lower() not in text, text


def test_render_truncates_long_error_with_ellipsis() -> None:
    long_e = "x" * 400
    snap = _health_snapshot(ping_last_error=long_e)
    text = _render_to_text(render_status_panel(snap, now=REF_TS), width=300)
    assert "…" in text
    # Truncated body must be strictly shorter than the raw input.
    assert len(long_e) > MAX_ERROR_CHARS
    assert "x" * (MAX_ERROR_CHARS + 5) not in text


def test_render_fetch_error_with_no_snapshot() -> None:
    text = _render_to_text(
        render_status_panel(None, now=REF_TS, fetch_error="httpx: connection dead"),
    )
    assert "connection dead" in text
    assert "null" in text


def test_render_fetch_error_overrides_task_error() -> None:
    snap = _health_snapshot(ping_last_error="stale ping error")
    text = _render_to_text(
        render_status_panel(snap, now=REF_TS, fetch_error="fresh fetch failure"),
    )
    assert "fresh fetch failure" in text
    assert "stale ping error" not in text


# ---------------------------------------------------------------------------
# _first_task_last_error — explicit priority
# ---------------------------------------------------------------------------


def test_first_task_error_prefers_ping_loop_over_others() -> None:
    tasks = {
        "chat_sse": {"last_error": "sse dead"},
        "command_executor": {"last_error": "executor dead"},
        "ping_loop": {"last_error": "ping dead"},
    }
    assert _first_task_last_error(tasks) == "ping dead"


def test_first_task_error_falls_back_to_chat_when_ping_silent() -> None:
    tasks = {
        "chat_sse": {"last_error": "sse dead"},
        "command_executor": {"last_error": "executor dead"},
        "ping_loop": {"last_error": None},
    }
    assert _first_task_last_error(tasks) == "sse dead"


def test_first_task_error_handles_unknown_tasks_alphabetically() -> None:
    tasks = {
        "aaa_custom": {"last_error": "custom a"},
        "zzz_custom": {"last_error": "custom z"},
    }
    assert _first_task_last_error(tasks) == "custom a"


def test_first_task_error_ignores_non_string_values() -> None:
    tasks = {"ping_loop": {"last_error": 42}, "chat_sse": {"last_error": "real"}}
    assert _first_task_last_error(tasks) == "real"


# ---------------------------------------------------------------------------
# clock skew — future timestamps
# ---------------------------------------------------------------------------


def test_ping_within_future_tolerance_renders_as_zero_seconds() -> None:
    assert _parse_last_success_ago(
        last_success_at=_iso(offset_s=2.0), now=REF_TS
    ) == 0


def test_ping_beyond_future_tolerance_is_null() -> None:
    future = _iso(offset_s=float(FUTURE_SKEW_TOLERANCE_S + 10))
    assert _parse_last_success_ago(last_success_at=future, now=REF_TS) is None
    snap = _health_snapshot(ping_last_success_at=future)
    text = _render_to_text(render_status_panel(snap, now=REF_TS))
    assert "null" in text


def test_ping_invalid_timestamp_is_null() -> None:
    assert _parse_last_success_ago(last_success_at="not-a-date", now=REF_TS) is None
    assert _parse_last_success_ago(last_success_at="", now=REF_TS) is None
    assert _parse_last_success_ago(last_success_at=None, now=REF_TS) is None
    assert _parse_last_success_ago(last_success_at=123, now=REF_TS) is None


# ---------------------------------------------------------------------------
# run_status_watch — integration-lite with real rich.live.Live
# ---------------------------------------------------------------------------


def _make_sleep_then_interrupt(n_ticks: int) -> Any:
    state = {"n": 0}

    def _sleep(_d: float) -> None:
        state["n"] += 1
        if state["n"] >= n_ticks:
            raise KeyboardInterrupt

    return _sleep, state


def _spy_live_update(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[RenderableType, bool]], list[int]]:
    """Record every ``Live.update`` call and count ``Live.refresh`` invocations."""
    updates: list[tuple[RenderableType, bool]] = []
    refreshes: list[int] = []

    original_update = Live.update
    original_refresh = Live.refresh

    def traced_update(self: Live, renderable: RenderableType, *, refresh: bool = False) -> None:
        updates.append((renderable, refresh))
        original_update(self, renderable, refresh=refresh)

    def traced_refresh(self: Live) -> None:
        refreshes.append(1)
        original_refresh(self)

    monkeypatch.setattr(Live, "update", traced_update)
    monkeypatch.setattr(Live, "refresh", traced_refresh)
    return updates, refreshes


def _render_snapshot_text(r: RenderableType) -> str:
    buf = _plain_console(width=120)
    buf.print(r)
    return buf.export_text().lower()


def test_real_live_refreshes_every_tick_and_leaves_final_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for C1 + H1: each update uses refresh=True and last frame persists."""
    updates, refreshes = _spy_live_update(monkeypatch)

    timestamps = [_iso(offset_s=-1.0), _iso(offset_s=-2.0), _iso(offset_s=-3.0)]
    call_n = {"n": 0}

    def get_snapshot(_: str) -> dict[str, Any]:
        i = call_n["n"]
        call_n["n"] += 1
        idx = min(i, len(timestamps) - 1)
        return _health_snapshot(ping_last_success_at=timestamps[idx])

    sleep_fn, _ = _make_sleep_then_interrupt(3)
    console = _plain_console(width=120)

    rc = run_status_watch(
        "http://127.0.0.1:8001",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0

    assert len(updates) >= 2, "Expected at least two live.update calls"
    assert all(refresh for _r, refresh in updates), (
        "Every live.update must be called with refresh=True (C1)"
    )
    rendered = [_render_snapshot_text(r) for r, _refresh in updates]
    # First frame goes into Live() constructor; subsequent frames arrive via update()
    assert any("2s ago" in f for f in rendered)
    assert any("3s ago" in f for f in rendered)

    final_text = console.export_text().lower()
    assert "3s ago" in final_text
    assert "watch stopped" in final_text
    assert len(refreshes) >= len(updates)


def test_run_status_watch_survives_request_error_and_renders_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates, _ = _spy_live_update(monkeypatch)

    call_n = {"n": 0}
    good = _health_snapshot(ping_last_success_at=_iso(offset_s=-1.0))

    def get_snapshot(_: str) -> dict[str, Any]:
        call_n["n"] += 1
        if call_n["n"] == 2:
            raise httpx.ConnectError("refused")
        return good

    sleep_fn, _ = _make_sleep_then_interrupt(3)
    console = _plain_console(width=120)

    rc = run_status_watch(
        "http://127.0.0.1:8001",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0
    assert call_n["n"] >= 2

    rendered = [_render_snapshot_text(r) for r, _refresh in updates]
    assert any("refused" in f for f in rendered), rendered
    assert "watch stopped" in console.export_text().lower()


@pytest.mark.parametrize(
    "raise_where",
    ["sleep", "get_snapshot", "now", "initial_fetch"],
)
def test_run_status_watch_keyboard_interrupt_from_any_stage_returns_zero(
    raise_where: str,
) -> None:
    ticks = {"n": 0}
    good = _health_snapshot(ping_last_success_at=_iso(offset_s=-1.0))

    def get_snapshot(_: str) -> dict[str, Any]:
        ticks["n"] += 1
        if raise_where == "initial_fetch" and ticks["n"] == 1:
            raise KeyboardInterrupt
        if raise_where == "get_snapshot" and ticks["n"] == 2:
            raise KeyboardInterrupt
        return good

    def sleep_fn(_d: float) -> None:
        if raise_where == "sleep":
            raise KeyboardInterrupt

    now_calls = {"n": 0}

    def now_fn() -> float:
        now_calls["n"] += 1
        # First call happens before the loop; interrupt on the 3rd → inside loop body
        if raise_where == "now" and now_calls["n"] >= 3:
            raise KeyboardInterrupt
        return REF_TS

    console = _plain_console(width=80)
    rc = run_status_watch(
        "http://x",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        sleep=sleep_fn,
        now=now_fn,
    )
    assert rc == 0
    assert "watch stopped" in console.export_text().lower()


def test_run_status_watch_snapshot_exception_is_displayed_not_raised() -> None:
    """Any non-KBI exception from get_snapshot is captured into Last error."""

    def get_snapshot(_: str) -> dict[str, Any]:
        raise RuntimeError("boom: /health parsing failed")

    sleep_fn, _ = _make_sleep_then_interrupt(2)
    console = _plain_console(width=120)
    rc = run_status_watch(
        "http://x",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0
    text = console.export_text().lower()
    assert "boom: /health parsing failed" in text


def test_run_status_watch_non_dict_response_surfaces_error() -> None:
    def get_snapshot(_: str) -> Any:
        return "not a dict"

    sleep_fn, _ = _make_sleep_then_interrupt(2)
    console = _plain_console(width=120)
    rc = run_status_watch(
        "http://x",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0
    text = console.export_text().lower()
    assert "invalid /health response" in text


def test_run_status_watch_uses_live_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke-test that ``rich.live.Live`` is the default factory."""
    entered = {"ok": False}

    original_enter = Live.__enter__

    def traced(self: Live) -> Live:
        entered["ok"] = True
        return original_enter(self)

    monkeypatch.setattr(Live, "__enter__", traced)

    def get_snapshot(_: str) -> dict[str, Any]:
        return _health_snapshot(ping_last_success_at=_iso(offset_s=-1.0))

    def sleep_fn(_d: float) -> None:
        raise KeyboardInterrupt

    rc = run_status_watch(
        "http://x",
        _plain_console(),
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0
    assert entered["ok"] is True


def test_watch_stopped_hint_is_english_consistent_with_setup_cli() -> None:
    """Message must not mix languages (see M5)."""
    cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in WATCH_STOPPED_HINT)
    assert not cyrillic, WATCH_STOPPED_HINT
