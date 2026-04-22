"""Live `setup` status view — Rich Live panel from ``GET /health`` snapshots."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from rich.console import Console, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

MAX_ERROR_CHARS = 200
PING_FRESH_S = 30
PING_WARN_S = 120
FUTURE_SKEW_TOLERANCE_S = 5

# Stable priority for picking a single "Last error" line out of the tasks map.
# ``ping_loop`` is the primary signal for cloud health and should win over
# ``chat_sse``/``command_executor`` even when they error first.
_TASK_ERROR_PRIORITY: tuple[str, ...] = ("ping_loop", "chat_sse", "command_executor")

WATCH_STOPPED_HINT = (
    "[hint]Watch stopped. Container keeps running. "
    "`./setup.sh status` — snapshot, `./setup.sh stop` — stop.[/hint]"
)


@runtime_checkable
class _LiveLike(Protocol):
    """Subset of ``rich.live.Live`` we rely on in ``run_status_watch``."""

    def __enter__(self) -> _LiveLike: ...
    def __exit__(self, *exc: object) -> bool | None: ...
    def update(self, renderable: RenderableType, *, refresh: bool = ...) -> None: ...


LiveFactory = Callable[..., AbstractContextManager[Any]]


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def _truncate_error(text: str) -> str:
    t = (text or "").replace("\n", " ").replace("\r", " ").strip()
    if len(t) <= MAX_ERROR_CHARS:
        return t
    return t[: MAX_ERROR_CHARS - 1] + "…"


def _parse_last_success_ago(*, last_success_at: object, now: float) -> int | None:
    if not isinstance(last_success_at, str) or not last_success_at.strip():
        return None
    try:
        raw = last_success_at.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        delta = now - dt.timestamp()
    except (OSError, OverflowError, ValueError):
        return None
    # Clock skew: agent timestamp ahead of ours — unreliable, treat as unknown.
    if delta < -FUTURE_SKEW_TOLERANCE_S:
        return None
    return max(0, int(delta))


def _age_style(age_s: int | None) -> str:
    if age_s is None:
        return "bold red"
    if age_s < PING_FRESH_S:
        return "green"
    if age_s < PING_WARN_S:
        return "yellow"
    return "bold red"


def _first_task_last_error(tasks: dict[str, Any]) -> str | None:
    """Pick the most relevant non-empty ``last_error`` from the tasks map.

    Priority: ``ping_loop`` first (primary cloud-health signal), then
    ``chat_sse``, then ``command_executor``. Anything else falls back to
    alphabetical order so unknown tasks don't silently hijack the slot.
    """
    ordered: list[str] = [name for name in _TASK_ERROR_PRIORITY if name in tasks]
    ordered += [name for name in sorted(tasks) if name not in _TASK_ERROR_PRIORITY]
    for name in ordered:
        t = tasks.get(name)
        if not isinstance(t, dict):
            continue
        err = t.get("last_error")
        if isinstance(err, str) and err.strip():
            return err.strip()
    return None


def _ping_row(snapshot: dict[str, Any] | None, *, now: float) -> Text:
    last_at: object | None = None
    if isinstance(snapshot, dict):
        tasks = snapshot.get("tasks")
        if isinstance(tasks, dict):
            raw_ping = tasks.get("ping_loop")
            if isinstance(raw_ping, dict):
                last_at = raw_ping.get("last_success_at")
    age = _parse_last_success_ago(last_success_at=last_at, now=now)
    if age is None:
        return Text("null", style="bold red")
    return Text(f"{age}s ago", style=_age_style(age))


def render_status_panel(
    snapshot: dict[str, Any] | None,
    *,
    now: float,
    fetch_error: str | None = None,
) -> RenderableType:
    if snapshot is None:
        session_label, session_style = "unknown", "dim"
        chat_text, chat_style = "unknown", "dim"
        err_source: str | None = fetch_error
    else:
        session = snapshot.get("session")
        if not isinstance(session, dict):
            session = {}
        sc = session.get("connected")
        if sc is True:
            session_label, session_style = "connected", "green"
        elif sc is False:
            session_label, session_style = "disconnected", "bold red"
        else:
            session_label, session_style = "unknown", "dim"

        tasks = snapshot.get("tasks")
        if not isinstance(tasks, dict):
            tasks = {}
        raw_chat = tasks.get("chat_sse")
        chat: dict[str, Any] = raw_chat if isinstance(raw_chat, dict) else {}
        cc = chat.get("connected")
        if cc is True:
            chat_text, chat_style = "connected", "green"
        elif cc is False:
            chat_text, chat_style = "reconnecting", "yellow"
        else:
            chat_text, chat_style = "unknown", "dim"

        err_source = fetch_error if fetch_error else _first_task_last_error(tasks)

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", style="dim", no_wrap=True)
    table.add_column(justify="left")

    table.add_row("Cloud session", Text(session_label, style=session_style))
    table.add_row("Chat SSE", Text(chat_text, style=chat_style))
    table.add_row("Last cloud ping", _ping_row(snapshot, now=now))

    if err_source:
        table.add_row("Last error", Text(_truncate_error(err_source), style="red"))

    return Panel(
        table,
        title="Live status",
        border_style="cyan",
        padding=(0, 1),
    )


def _safe_get_snapshot(
    get_snapshot: Callable[[str], dict[str, Any]], base_url: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = get_snapshot(base_url)
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberately broad: watch must not die on any IO error
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(raw, dict):
        return None, "Invalid /health response: not a JSON object"
    return raw, None


def run_status_watch(
    base_url: str,
    console: Console,
    *,
    poll_interval: float = 2.0,
    get_snapshot: Callable[[str], dict[str, Any]],
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    live_factory: LiveFactory | None = None,
) -> int:
    """Render a Rich Live panel polling ``/health`` every ``poll_interval``.

    The loop is resilient to snapshot fetch errors (captured into ``Last error``)
    and returns ``0`` on ``KeyboardInterrupt`` after printing a one-line hint and
    leaving the final panel visible. Every update forces a refresh because we run
    with ``auto_refresh=False`` to avoid redundant redraws.
    """
    factory: LiveFactory = live_factory if live_factory is not None else Live

    def _snapshot() -> tuple[dict[str, Any] | None, str | None]:
        try:
            return _safe_get_snapshot(get_snapshot, base_url)
        except KeyboardInterrupt:
            raise

    try:
        snap, err = _snapshot()
        last_panel = render_status_panel(snap, now=now(), fetch_error=err)
        with factory(
            last_panel,
            console=console,
            auto_refresh=False,
            transient=False,
        ) as live:
            while True:
                sleep(poll_interval)
                snap, err = _snapshot()
                last_panel = render_status_panel(snap, now=now(), fetch_error=err)
                live.update(last_panel, refresh=True)
    except KeyboardInterrupt:
        pass

    console.print(WATCH_STOPPED_HINT)
    return 0
