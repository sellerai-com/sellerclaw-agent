"""Live `setup` status view — human-readable Rich panel from local data sources.

The panel is aimed at non-technical users: it hides framework-level jargon
(sessions, SSE, ping loop) behind plain labels like "Cloud connection" or
"Chat with agent" and summarises the manifest (enabled modules, connected
integrations, browser, web search, Telegram) so the user can tell at a glance
whether the installation is correctly configured.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
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

_TASK_ERROR_PRIORITY: tuple[str, ...] = ("ping_loop", "chat_sse", "hooks_sse", "command_executor")

WATCH_STOPPED_HINT = (
    "[hint]Watch stopped. Container keeps running. "
    "`./setup.sh status` — snapshot, `./setup.sh stop` — stop.[/hint]"
)

# Labels in the panel's left column. Kept short for alignment.
_LABEL_CLOUD = "Cloud connection"
_LABEL_AGENT = "Agent (OpenClaw)"
_LABEL_CHAT = "Chat with agent"
_LABEL_BROWSER = "Browser"
_LABEL_MODULES = "Active modules"
_LABEL_INTEGRATIONS = "Integrations"
_LABEL_WEB_SEARCH = "Web search"
_LABEL_TELEGRAM = "Telegram"
_LABEL_ERROR = "Last error"

# Manifest enum → human label. Order matters: it's the rendering order.
_MODULE_NAMES: dict[str, str] = {
    "product_scout": "Product Scout",
    "dropshipping_supplier": "Dropshipping Supplier",
    "shopify_store_manager": "Shopify Store",
    "ebay_store_manager": "eBay Store",
    "marketing_manager": "Marketing Manager",
}

_INTEGRATION_NAMES: dict[str, str] = {
    "shopify_store": "Shopify store",
    "shopify_themes": "Shopify themes",
    "ebay_store": "eBay store",
    "supplier_cj": "CJ Dropshipping",
    "supplier_any": "Supplier (generic)",
    "facebook_ads": "Facebook Ads",
    "google_ads": "Google Ads",
    "research_trends": "Google Trends",
    "research_seo": "SEO research (DataForSEO)",
    "research_social": "Social research (SociaVault)",
}


@runtime_checkable
class _LiveLike(Protocol):
    def __enter__(self) -> _LiveLike: ...
    def __exit__(self, *exc: object) -> bool | None: ...
    def update(self, renderable: RenderableType, *, refresh: bool = ...) -> None: ...


LiveFactory = Callable[..., AbstractContextManager[Any]]


# ---------------------------------------------------------------------------
# Plain-text helpers
# ---------------------------------------------------------------------------


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


def _format_last_sync(age_s: int | None) -> tuple[str, str]:
    """Human-readable last-sync text + Rich style."""
    if age_s is None:
        return "no sync yet", "bold red"
    if age_s < 5:
        return "last sync just now", "green"
    if age_s < PING_FRESH_S:
        return f"last sync {age_s}s ago", "green"
    if age_s < PING_WARN_S:
        return f"last sync {age_s}s ago", "yellow"
    minutes = age_s // 60
    if minutes < 60:
        return f"last sync over {minutes} min ago", "bold red"
    hours = minutes // 60
    return f"last sync over {hours}h ago", "bold red"


def _format_uptime(seconds: float | int | None) -> str:
    if seconds is None:
        return ""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        return ""
    if s < 60:
        return f"{s}s"
    minutes, sec = divmod(s, 60)
    if minutes < 60:
        if sec == 0:
            return f"{minutes}m"
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        if minutes == 0:
            return f"{hours}h"
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    if hours == 0:
        return f"{days}d"
    return f"{days}d {hours}h"


def _first_task_last_error(tasks: dict[str, Any]) -> str | None:
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


# ---------------------------------------------------------------------------
# Manifest loading (best-effort; missing/invalid manifest is not fatal)
# ---------------------------------------------------------------------------


# Cache keyed by ``(data_dir, mtime_ns, size)`` — ``setup`` refreshes the screen
# every ~1s, and re-parsing the same manifest each tick is pure waste. We key on
# ``stat`` rather than a timestamp so atomic writes via ``os.replace`` are picked
# up the moment the new inode lands.
_MANIFEST_CACHE: dict[Path, tuple[int, int, dict[str, Any] | None]] = {}


def load_manifest_from_disk(data_dir: Path) -> dict[str, Any] | None:
    path = data_dir / "manifest.json"
    try:
        st = path.stat()
    except OSError:
        _MANIFEST_CACHE.pop(data_dir, None)
        return None
    cached = _MANIFEST_CACHE.get(data_dir)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        _MANIFEST_CACHE.pop(data_dir, None)
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    value: dict[str, Any] | None = parsed if isinstance(parsed, dict) else None
    _MANIFEST_CACHE[data_dir] = (st.st_mtime_ns, st.st_size, value)
    return value


def _humanize_modules(modules: object) -> list[str]:
    if not isinstance(modules, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for key, label in _MODULE_NAMES.items():
        if key in modules and key not in seen:
            out.append(label)
            seen.add(key)
    for item in modules:
        if isinstance(item, str) and item not in seen and item not in _MODULE_NAMES:
            out.append(item.replace("_", " ").title())
            seen.add(item)
    return out


def _humanize_integrations(integrations: object) -> list[str]:
    if not isinstance(integrations, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for key, label in _INTEGRATION_NAMES.items():
        if key in integrations and key not in seen:
            out.append(label)
            seen.add(key)
    for item in integrations:
        if isinstance(item, str) and item not in seen and item not in _INTEGRATION_NAMES:
            out.append(item.replace("_", " ").title())
            seen.add(item)
    return out


def _browser_summary(manifest: dict[str, Any]) -> tuple[str, str]:
    enabled = manifest.get("global_browser_enabled")
    per_module = manifest.get("per_module_browser")
    active_modules = manifest.get("enabled_modules")
    if enabled is False:
        return "off", "yellow"
    if enabled is not True:
        return "unknown", "dim"
    active_count = 0
    total_count = 0
    if isinstance(per_module, dict) and isinstance(active_modules, list):
        total_count = sum(1 for m in active_modules if isinstance(m, str))
        active_count = sum(
            1
            for m in active_modules
            if isinstance(m, str) and bool(per_module.get(m, True))
        )
    if total_count and active_count == total_count:
        return f"on (available to all {total_count} modules)", "green"
    if total_count:
        return f"on ({active_count} of {total_count} modules)", "green"
    return "on", "green"


def _bool_onoff(value: object) -> tuple[str, str]:
    if value is True:
        return "on", "green"
    if value is False:
        return "off", "dim"
    return "unknown", "dim"


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _cloud_row(snapshot: dict[str, Any] | None, *, now: float) -> tuple[str, str]:
    """Return (text, style) summarising cloud/agent reachability."""
    if snapshot is None:
        return "unreachable — is the container running?", "bold red"
    session = snapshot.get("session") if isinstance(snapshot.get("session"), dict) else {}
    assert isinstance(session, dict)
    connected = session.get("connected")
    ping: dict[str, Any] = {}
    tasks = snapshot.get("tasks")
    if isinstance(tasks, dict):
        raw_ping = tasks.get("ping_loop")
        if isinstance(raw_ping, dict):
            ping = raw_ping
    age = _parse_last_success_ago(last_success_at=ping.get("last_success_at"), now=now)
    sync_text, sync_style = _format_last_sync(age)

    if connected is True:
        prefix = "connected"
        if age is not None and age < PING_WARN_S:
            return f"{prefix} ({sync_text})", "green"
        return f"{prefix} ({sync_text})", sync_style
    if connected is False:
        return "not signed in — run `./setup.sh` to connect", "bold red"
    return "unknown", "dim"


def _is_signed_in(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    session = snapshot.get("session")
    return isinstance(session, dict) and session.get("connected") is True


def _agent_row(
    snapshot: dict[str, Any] | None,
    *,
    signed_in: bool,
    manifest_loaded: bool,
) -> tuple[str, str]:
    if snapshot is None:
        return "unknown", "dim"
    oc = snapshot.get("openclaw")
    if not isinstance(oc, dict):
        return "unknown", "dim"
    status = oc.get("status")
    uptime = _format_uptime(oc.get("uptime_seconds"))
    if status == "running":
        if uptime:
            return f"running for {uptime}", "green"
        return "running", "green"
    if status == "starting":
        return "starting up…", "yellow"
    if status == "stopped":
        # Fresh install / just signed in: OpenClaw starts only after the cloud
        # pushes the manifest. Do not alarm the user with red "stopped" when
        # this is just the normal waiting phase.
        if signed_in and not manifest_loaded:
            return "waiting for configuration from cloud…", "yellow"
        return "stopped", "bold red"
    if status == "error":
        err = oc.get("error")
        if isinstance(err, str) and err.strip():
            return f"error: {_truncate_error(err)}", "bold red"
        return "error", "bold red"
    if not status:
        return "unknown", "dim"
    return str(status), "dim"


def _chat_row(
    snapshot: dict[str, Any] | None,
    *,
    signed_in: bool,
    manifest_loaded: bool,
) -> tuple[str, str]:
    if snapshot is None:
        return "unknown", "dim"
    tasks = snapshot.get("tasks")
    if not isinstance(tasks, dict):
        return "unknown", "dim"
    chat = tasks.get("chat_sse")
    if not isinstance(chat, dict):
        return "unknown", "dim"
    connected = chat.get("connected")
    if connected is True:
        return "available", "green"
    if connected is False:
        # The chat loop refuses to dial out until a manifest is on disk
        # (see sellerclaw_agent/cloud/chat_listener.py). Before that happens
        # the cloud has to push a `start` command, which saves the manifest.
        # So "not connected + signed in + no manifest" is not a reconnect —
        # the channel just hasn't been needed yet.
        if signed_in and not manifest_loaded:
            return "will connect when agent starts", "yellow"
        return "reconnecting — should come up shortly", "yellow"
    return "unknown", "dim"


def _wrap_list(items: list[str], *, empty: str, empty_style: str) -> tuple[str, str]:
    if not items:
        return empty, empty_style
    return ", ".join(items), ""


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------


def render_status_panel(
    snapshot: dict[str, Any] | None,
    *,
    now: float,
    manifest: dict[str, Any] | None = None,
    fetch_error: str | None = None,
) -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", style="dim", no_wrap=True)
    table.add_column(justify="left", overflow="fold")

    signed_in = _is_signed_in(snapshot)
    manifest_loaded = isinstance(manifest, dict)

    cloud_text, cloud_style = _cloud_row(snapshot, now=now)
    table.add_row(_LABEL_CLOUD, Text(cloud_text, style=cloud_style))

    agent_text, agent_style = _agent_row(
        snapshot, signed_in=signed_in, manifest_loaded=manifest_loaded
    )
    table.add_row(_LABEL_AGENT, Text(agent_text, style=agent_style))

    chat_text, chat_style = _chat_row(
        snapshot, signed_in=signed_in, manifest_loaded=manifest_loaded
    )
    table.add_row(_LABEL_CHAT, Text(chat_text, style=chat_style))

    if not manifest_loaded:
        if signed_in:
            # Manifest is pushed by the cloud over chat SSE; on a fresh install
            # it may take a few seconds to arrive. Be explicit so the user
            # does not think something is broken.
            table.add_row(
                "Personalisation",
                Text("loading from cloud…", style="yellow"),
            )
        else:
            table.add_row(
                "Personalisation",
                Text("will appear after sign-in", style="dim"),
            )
    else:
        assert isinstance(manifest, dict)
        browser_text, browser_style = _browser_summary(manifest)
        table.add_row(_LABEL_BROWSER, Text(browser_text, style=browser_style))

        modules_names = _humanize_modules(manifest.get("enabled_modules"))
        modules_text, modules_style = _wrap_list(
            modules_names, empty="none", empty_style="dim"
        )
        table.add_row(_LABEL_MODULES, Text(modules_text, style=modules_style))

        integrations_names = _humanize_integrations(
            manifest.get("connected_integrations")
        )
        integrations_text, integrations_style = _wrap_list(
            integrations_names, empty="none connected", empty_style="dim"
        )
        table.add_row(_LABEL_INTEGRATIONS, Text(integrations_text, style=integrations_style))

        web = manifest.get("web_search")
        web_enabled = web.get("enabled") if isinstance(web, dict) else None
        web_text, web_style = _bool_onoff(web_enabled)
        table.add_row(_LABEL_WEB_SEARCH, Text(web_text, style=web_style))

        tg = manifest.get("telegram")
        tg_enabled = tg.get("enabled") if isinstance(tg, dict) else None
        tg_text, tg_style = _bool_onoff(tg_enabled)
        table.add_row(_LABEL_TELEGRAM, Text(tg_text, style=tg_style))

    err_source: str | None = fetch_error
    if err_source is None and isinstance(snapshot, dict):
        tasks = snapshot.get("tasks")
        if isinstance(tasks, dict):
            err_source = _first_task_last_error(tasks)
    if err_source:
        table.add_row(_LABEL_ERROR, Text(_truncate_error(err_source), style="red"))

    return Panel(
        table,
        title="SellerClaw status",
        subtitle="press Ctrl+C to stop watching",
        subtitle_align="right",
        border_style="cyan",
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------


def _safe_get_snapshot(
    get_snapshot: Callable[[str], dict[str, Any]], base_url: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = get_snapshot(base_url)
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 - watch must not die on any IO error
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(raw, dict):
        return None, "Invalid /health response: not a JSON object"
    return raw, None


def _safe_get_manifest(
    get_manifest: Callable[[], dict[str, Any] | None] | None,
) -> dict[str, Any] | None:
    if get_manifest is None:
        return None
    try:
        result = get_manifest()
    except KeyboardInterrupt:
        raise
    except Exception:  # noqa: BLE001 - manifest is optional, never block the watch
        return None
    return result if isinstance(result, dict) else None


def run_status_watch(
    base_url: str,
    console: Console,
    *,
    poll_interval: float = 2.0,
    get_snapshot: Callable[[str], dict[str, Any]],
    get_manifest: Callable[[], dict[str, Any] | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    live_factory: LiveFactory | None = None,
) -> int:
    """Render a Rich Live panel polling ``/health`` every ``poll_interval``.

    ``KeyboardInterrupt`` at any stage yields a clean exit with a one-line hint
    and code ``0``. Every update forces a refresh so the terminal reflects the
    latest state even though ``auto_refresh`` is disabled.
    """
    factory: LiveFactory = live_factory if live_factory is not None else Live

    try:
        snap, err = _safe_get_snapshot(get_snapshot, base_url)
        manifest = _safe_get_manifest(get_manifest)
        panel = render_status_panel(snap, now=now(), manifest=manifest, fetch_error=err)
        with factory(
            panel,
            console=console,
            auto_refresh=False,
            transient=False,
        ) as live:
            while True:
                sleep(poll_interval)
                snap, err = _safe_get_snapshot(get_snapshot, base_url)
                manifest = _safe_get_manifest(get_manifest)
                panel = render_status_panel(
                    snap, now=now(), manifest=manifest, fetch_error=err
                )
                live.update(panel, refresh=True)
    except KeyboardInterrupt:
        pass

    console.print(WATCH_STOPPED_HINT)
    return 0
