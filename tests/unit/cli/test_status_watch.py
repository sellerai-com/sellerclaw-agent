"""Unit tests for live ``setup`` status watch (Rich panel + control loop)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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
    _format_uptime,
    _parse_last_success_ago,
    _humanize_integrations,
    _humanize_modules,
    load_manifest_from_disk,
    render_status_panel,
    run_status_watch,
)

pytestmark = pytest.mark.unit


REF_TS = 1_700_000_000.0


def _plain_console(width: int = 120) -> Console:
    return Console(
        record=True,
        width=width,
        force_terminal=True,
        color_system="truecolor",
    )


def _render_to_text(renderable: RenderableType, *, width: int = 120) -> str:
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
    openclaw_status: str | None = "running",
    openclaw_uptime_seconds: float | int | None = 8054,
    openclaw_error: str | None = None,
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
        "openclaw": {
            "status": openclaw_status,
            "uptime_seconds": openclaw_uptime_seconds,
            "error": openclaw_error,
        },
    }


def _full_manifest() -> dict[str, Any]:
    return {
        "enabled_modules": [
            "product_scout",
            "dropshipping_supplier",
            "shopify_store_manager",
            "ebay_store_manager",
            "marketing_manager",
        ],
        "connected_integrations": [
            "shopify_store",
            "supplier_cj",
            "research_trends",
            "research_seo",
        ],
        "global_browser_enabled": True,
        "per_module_browser": {
            "product_scout": True,
            "dropshipping_supplier": True,
            "shopify_store_manager": True,
            "ebay_store_manager": True,
            "marketing_manager": True,
        },
        "web_search": {"enabled": True},
        "telegram": {"enabled": False},
    }


# ---------------------------------------------------------------------------
# User-facing panel content
# ---------------------------------------------------------------------------


def test_panel_shows_human_labels_no_jargon_when_healthy() -> None:
    snap = _health_snapshot(ping_last_success_at=_iso(offset_s=-3.0))
    text = _render_to_text(
        render_status_panel(snap, now=REF_TS, manifest=_full_manifest())
    )
    # Positive: human labels present
    assert "cloud connection" in text
    assert "connected" in text
    assert "agent (openclaw)" in text
    assert "running for 2h 14m" in text
    assert "chat with agent" in text
    assert "available" in text
    assert "browser" in text
    assert "active modules" in text
    assert "product scout" in text
    assert "shopify store" in text
    assert "integrations" in text
    assert "cj dropshipping" in text
    assert "google trends" in text
    assert "web search" in text
    assert "telegram" in text
    # Negative: no raw jargon
    assert "chat sse" not in text
    assert "last cloud ping" not in text
    assert "cloud session" not in text
    assert "ping_loop" not in text


def test_panel_without_manifest_when_signed_in_says_loading() -> None:
    """Fresh install after sign-in: manifest hasn't arrived from cloud yet.

    The label must make it clear that data is on the way — not hint the user
    to sign in again.
    """
    snap = _health_snapshot(
        session_connected=True,
        ping_last_success_at=_iso(offset_s=-1.0),
    )
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=None))
    assert "personalisation" in text
    assert "loading from cloud" in text
    assert "will appear after sign-in" not in text
    assert "active modules" not in text
    assert "integrations" not in text


def test_panel_without_manifest_when_not_signed_in_mentions_signin() -> None:
    snap = _health_snapshot(
        session_connected=False, ping_last_success_at=None
    )
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=None))
    assert "personalisation" in text
    assert "will appear after sign-in" in text
    assert "loading from cloud" not in text


def test_agent_stopped_while_waiting_for_manifest_is_friendly() -> None:
    """Right after sign-in OpenClaw is `stopped` until the cloud pushes the
    manifest. We must not scream red — explain the normal waiting state.
    """
    snap = _health_snapshot(
        session_connected=True,
        ping_last_success_at=_iso(offset_s=-1.0),
        openclaw_status="stopped",
        openclaw_uptime_seconds=None,
    )
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=None))
    assert "waiting for configuration from cloud" in text


def test_agent_stopped_after_manifest_is_still_stopped() -> None:
    """When the user has a manifest but explicitly stopped the agent we keep
    the plain `stopped` message (red) — that IS a problem worth flagging.
    """
    snap = _health_snapshot(
        session_connected=True,
        ping_last_success_at=_iso(offset_s=-1.0),
        openclaw_status="stopped",
        openclaw_uptime_seconds=None,
    )
    text = _render_to_text(
        render_status_panel(snap, now=REF_TS, manifest=_full_manifest())
    )
    assert "stopped" in text
    assert "waiting for configuration" not in text


def test_panel_when_snapshot_missing_says_unreachable() -> None:
    text = _render_to_text(
        render_status_panel(None, now=REF_TS, manifest=None, fetch_error="boom")
    )
    assert "unreachable" in text
    assert "boom" in text


def test_panel_when_not_signed_in_prompts_user() -> None:
    snap = _health_snapshot(session_connected=False, ping_last_success_at=None)
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=None))
    assert "not signed in" in text
    assert "./setup.sh" in text


def test_chat_when_signed_in_without_manifest_says_will_connect_on_start() -> None:
    """Fresh install: chat SSE will not dial out until the cloud pushes a
    `start` command (which writes the manifest). "Reconnecting" would be
    misleading — the channel has never connected yet.
    """
    snap = _health_snapshot(
        session_connected=True,
        ping_last_success_at=_iso(offset_s=-1.0),
        chat_connected=False,
    )
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=None))
    assert "will connect when agent starts" in text
    assert "reconnecting" not in text


def test_chat_reconnecting_when_manifest_present_but_sse_down() -> None:
    """Manifest already on disk but SSE is currently disconnected — this IS
    a real reconnect scenario and we should say so.
    """
    snap = _health_snapshot(
        session_connected=True,
        ping_last_success_at=_iso(offset_s=-1.0),
        chat_connected=False,
    )
    text = _render_to_text(
        render_status_panel(snap, now=REF_TS, manifest=_full_manifest())
    )
    assert "reconnecting" in text
    assert "shortly" in text
    assert "will connect when agent starts" not in text


def test_chat_reconnecting_when_not_signed_in_keeps_generic_message() -> None:
    snap = _health_snapshot(
        session_connected=False,
        ping_last_success_at=None,
        chat_connected=False,
    )
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=None))
    # We still surface reconnecting here; the signed-in/manifest override is
    # specifically for the fresh-install path.
    assert "reconnecting" in text


def test_openclaw_statuses_rendered_in_plain_english() -> None:
    # Each case explicitly decouples from the "stopped-while-waiting" special
    # case so we verify the base rendering for each state.
    cases = [
        # (status, expected, manifest, session_connected)
        ("running", "running for", None, True),
        ("starting", "starting up", None, True),
        ("stopped", "stopped", _full_manifest(), True),
    ]
    for status, expected, manifest, connected in cases:
        snap = _health_snapshot(
            session_connected=connected,
            ping_last_success_at=_iso(offset_s=-1.0),
            openclaw_status=status,
        )
        text = _render_to_text(
            render_status_panel(snap, now=REF_TS, manifest=manifest)
        )
        assert expected in text, (status, text)
        if status == "stopped":
            assert "waiting for configuration" not in text


def test_openclaw_error_is_visible() -> None:
    snap = _health_snapshot(
        ping_last_success_at=_iso(offset_s=-1.0),
        openclaw_status="error",
        openclaw_error="container failed to boot: image missing",
    )
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=None))
    assert "error" in text
    assert "image missing" in text


def test_browser_summary_reflects_per_module_setting() -> None:
    snap = _health_snapshot(ping_last_success_at=_iso(offset_s=-1.0))
    manifest = _full_manifest()
    manifest["per_module_browser"] = {
        "product_scout": True,
        "dropshipping_supplier": True,
        "shopify_store_manager": False,
        "ebay_store_manager": False,
        "marketing_manager": False,
    }
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=manifest))
    assert "on (2 of 5 modules)" in text


def test_browser_disabled_globally_is_off() -> None:
    snap = _health_snapshot(ping_last_success_at=_iso(offset_s=-1.0))
    manifest = _full_manifest()
    manifest["global_browser_enabled"] = False
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=manifest))
    assert "browser" in text
    assert "off" in text


def test_web_search_and_telegram_show_on_off() -> None:
    snap = _health_snapshot(ping_last_success_at=_iso(offset_s=-1.0))
    manifest = _full_manifest()
    manifest["web_search"] = {"enabled": False}
    manifest["telegram"] = {"enabled": True}
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=manifest))
    # Find the web search / telegram rows and check their values
    for line in text.splitlines():
        if "web search" in line:
            assert "off" in line
        if "telegram " in line or line.strip().startswith("telegram"):
            assert "on" in line


def test_panel_truncates_long_error() -> None:
    long_e = "x" * 400
    snap = _health_snapshot(
        ping_last_success_at=_iso(offset_s=-1.0),
        ping_last_error=long_e,
    )
    text = _render_to_text(render_status_panel(snap, now=REF_TS, manifest=None), width=300)
    assert "…" in text
    assert "x" * (MAX_ERROR_CHARS + 5) not in text


# ---------------------------------------------------------------------------
# Humanizers
# ---------------------------------------------------------------------------


def test_humanize_modules_uses_nice_names_and_keeps_unknowns() -> None:
    names = _humanize_modules(
        ["product_scout", "marketing_manager", "experimental_module"]
    )
    assert names[0] == "Product Scout"
    assert "Marketing Manager" in names
    assert "Experimental Module" in names  # title-cased fallback


def test_humanize_integrations_orders_by_catalog() -> None:
    names = _humanize_integrations(["supplier_cj", "shopify_store"])
    # Shopify should come before CJ per catalog order
    assert names.index("Shopify store") < names.index("CJ Dropshipping")


def test_humanize_handles_non_list_input() -> None:
    assert _humanize_modules(None) == []
    assert _humanize_integrations("nope") == []


# ---------------------------------------------------------------------------
# load_manifest_from_disk
# ---------------------------------------------------------------------------


def test_load_manifest_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_manifest_from_disk(tmp_path) is None


def test_load_manifest_returns_none_on_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("not json", encoding="utf-8")
    assert load_manifest_from_disk(tmp_path) is None


def test_load_manifest_returns_dict(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert load_manifest_from_disk(tmp_path) == {"a": 1}


def test_load_manifest_rejects_non_dict(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert load_manifest_from_disk(tmp_path) is None


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def test_format_uptime_humanizes_seconds() -> None:
    assert _format_uptime(0) == "0s"
    assert _format_uptime(59) == "59s"
    assert _format_uptime(60) == "1m"
    assert _format_uptime(65) == "1m 5s"
    assert _format_uptime(3600) == "1h"
    assert _format_uptime(3700) == "1h 1m"
    assert _format_uptime(90000) == "1d 1h"
    assert _format_uptime(None) == ""
    assert _format_uptime("invalid") == ""  # type: ignore[arg-type]
    assert _format_uptime(-1) == ""


def test_first_task_error_prefers_ping_loop() -> None:
    tasks = {
        "chat_sse": {"last_error": "sse dead"},
        "command_executor": {"last_error": "executor dead"},
        "ping_loop": {"last_error": "ping dead"},
    }
    assert _first_task_last_error(tasks) == "ping dead"


def test_clock_skew_beyond_tolerance_is_null() -> None:
    future = _iso(offset_s=float(FUTURE_SKEW_TOLERANCE_S + 10))
    assert _parse_last_success_ago(last_success_at=future, now=REF_TS) is None


def test_clock_skew_within_tolerance_is_zero() -> None:
    near_future = _iso(offset_s=2.0)
    assert _parse_last_success_ago(last_success_at=near_future, now=REF_TS) == 0


# ---------------------------------------------------------------------------
# run_status_watch — polling loop
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
    buf = _plain_console(width=160)
    buf.print(r)
    return buf.export_text().lower()


def test_live_refreshes_every_tick_and_leaves_final_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates, refreshes = _spy_live_update(monkeypatch)

    def get_snapshot(_: str) -> dict[str, Any]:
        return _health_snapshot(
            ping_last_success_at=_iso(offset_s=-1.0), openclaw_uptime_seconds=1
        )

    sleep_fn, _ = _make_sleep_then_interrupt(3)
    console = _plain_console()

    rc = run_status_watch(
        "http://x",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0
    assert len(updates) >= 2
    assert all(refresh for _r, refresh in updates)
    assert len(refreshes) >= len(updates)
    final = console.export_text().lower()
    assert "sellerclaw status" in final
    assert "watch stopped" in final


def test_watch_passes_manifest_to_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    updates, _ = _spy_live_update(monkeypatch)

    def get_snapshot(_: str) -> dict[str, Any]:
        return _health_snapshot(ping_last_success_at=_iso(offset_s=-1.0))

    manifest_calls = {"n": 0}

    def get_manifest() -> dict[str, Any]:
        manifest_calls["n"] += 1
        return _full_manifest()

    sleep_fn, _ = _make_sleep_then_interrupt(2)
    console = _plain_console()

    rc = run_status_watch(
        "http://x",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        get_manifest=get_manifest,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0
    assert manifest_calls["n"] >= 1
    rendered = [_render_snapshot_text(r) for r, _ in updates]
    assert any("product scout" in f for f in rendered)
    assert any("cj dropshipping" in f for f in rendered)


def test_watch_survives_manifest_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_snapshot(_: str) -> dict[str, Any]:
        return _health_snapshot(ping_last_success_at=_iso(offset_s=-1.0))

    def get_manifest() -> dict[str, Any]:
        raise RuntimeError("manifest file locked")

    sleep_fn, _ = _make_sleep_then_interrupt(2)
    console = _plain_console()
    rc = run_status_watch(
        "http://x",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        get_manifest=get_manifest,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0
    # Error from manifest must not surface; panel should fall back to "personalisation" line
    final = console.export_text().lower()
    assert "personalisation" in final
    assert "manifest file locked" not in final


def test_watch_captures_request_error_into_last_error(
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
    console = _plain_console()
    rc = run_status_watch(
        "http://x",
        console,
        poll_interval=0.0,
        get_snapshot=get_snapshot,
        sleep=sleep_fn,
        now=lambda: REF_TS,
    )
    assert rc == 0
    rendered = [_render_snapshot_text(r) for r, _ in updates]
    assert any("refused" in f for f in rendered)


@pytest.mark.parametrize(
    "raise_where",
    ["sleep", "get_snapshot", "now", "initial_fetch"],
)
def test_keyboard_interrupt_from_any_stage_returns_zero(raise_where: str) -> None:
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


def test_watch_stopped_hint_is_english_only() -> None:
    cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in WATCH_STOPPED_HINT)
    assert not cyrillic, WATCH_STOPPED_HINT
