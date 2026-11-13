"""Keeps an eye on the owner's subscription: signed in? limit used up? when does it reset?

Runs off the ping path, like the WhatsApp status monitor. Each pass writes ``status.json`` (the
sellerclaw-ui plugin reads it to hold turns while the agent waits) and keeps the ping payload the
cloud shows in Settings → Models and turns into Activity entries. Nothing here carries the owner's
identity or any credential.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from sellerclaw_agent.async_backoff import sleep_until
from sellerclaw_agent.bundle.manifest import bundle_manifest_from_mapping
from sellerclaw_agent.server.storage import ManifestStorage
from sellerclaw_agent.subscription import chatgpt_auth, claude_cli
from sellerclaw_agent.subscription.overlay import resolve_overlay
from sellerclaw_agent.subscription.state import (
    SubscriptionChoice,
    atomic_write_json,
    load_choice,
    read_json,
    status_path,
)

_log = structlog.get_logger(__name__)

POLL_SECONDS = 60.0
#: When Claude Code says the limit is used up without saying until when, try again after this.
UNKNOWN_RESET_RETRY = timedelta(minutes=30)

#: Claude Code's window names → the cloud's.
_CLAUDE_WINDOWS = {"five_hour": "5h", "seven_day": "week"}


def _parse_iso(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class SubscriptionStatus:
    """One observation of the subscription, ready for ``status.json`` and the ping."""

    provider: str
    state: str  # connected | limit_reached | signed_out
    observed_at: datetime
    plan: str | None = None
    main_model: str | None = None
    specialist_model: str | None = None
    windows: tuple[dict[str, Any], ...] = ()
    resumes_at: datetime | None = None
    #: When the plan last told us how full ``windows`` are. Claude says so only while the agent
    #: works, so a reading can be hours older than ``observed_at``; the panel shows its age.
    limits_observed_at: datetime | None = None

    def to_ping_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "state": self.state,
            "observed_at": self.observed_at.isoformat(),
            "plan": self.plan,
            "main_model": self.main_model,
            "specialist_model": self.specialist_model,
            "windows": list(self.windows),
            "resumes_at": self.resumes_at.isoformat() if self.resumes_at is not None else None,
            "limits_observed_at": (
                self.limits_observed_at.isoformat() if self.limits_observed_at is not None else None
            ),
        }

    def to_status_file(self) -> dict[str, Any]:
        """What the plugin's turn gate reads: whether to hold turns, and until when."""
        return {
            "provider": self.provider,
            "state": self.state,
            "observed_at": self.observed_at.isoformat(),
            "resumes_at": self.resumes_at.isoformat() if self.resumes_at is not None else None,
        }


def claude_status_from(
    *,
    logged_in: bool,
    plan: str | None,
    limits: dict[str, Any] | None,
    now: datetime,
    main_model: str | None,
    specialist_model: str | None,
) -> SubscriptionStatus:
    """Turn what Claude Code said (sign-in, its last ``rate_limit_event``) into one status."""
    windows: list[dict[str, Any]] = []
    rejected_until: list[datetime] = []
    rejected = False
    if limits is not None:
        raw_windows = limits.get("windows") if isinstance(limits.get("windows"), dict) else {}
        for name, label in _CLAUDE_WINDOWS.items():
            window = raw_windows.get(name) if isinstance(raw_windows, dict) else None
            if not isinstance(window, dict):
                continue
            utilization = window.get("utilization")
            reset_at = _parse_iso(window.get("resets_at"))
            if isinstance(utilization, (int, float)):
                windows.append(
                    {
                        "window": label,
                        "used_percent": max(0, min(100, round(float(utilization) * 100))),
                        "reset_at": reset_at.isoformat() if reset_at is not None else None,
                    }
                )
            if window.get("status") == "rejected":
                rejected = True
                if reset_at is not None:
                    rejected_until.append(reset_at)
        if limits.get("status") == "rejected":
            rejected = True
            overall = _parse_iso(limits.get("resets_at"))
            if overall is not None:
                rejected_until.append(overall)

    limits_observed_at = _parse_iso(limits.get("observed_at")) if limits is not None else None
    state = "connected"
    resumes_at: datetime | None = None
    if not logged_in:
        state = "signed_out"
    elif rejected:
        resumes_at = max(rejected_until) if rejected_until else (limits_observed_at or now) + UNKNOWN_RESET_RETRY
        if resumes_at > now:
            state = "limit_reached"
        else:
            resumes_at = None  # the window already reset; the next turn will tell
    return SubscriptionStatus(
        provider="claude",
        state=state,
        observed_at=now,
        plan=plan,
        main_model=main_model,
        specialist_model=specialist_model,
        windows=tuple(windows),
        resumes_at=resumes_at,
        limits_observed_at=limits_observed_at if windows else None,
    )


def _window_label(raw: object) -> str | None:
    """OpenClaw's window names for ChatGPT ("5h", "Week") → the cloud's ("5h", "week")."""
    text = str(raw or "").strip().lower().replace(" ", "")
    if not text:
        return None
    if text in {"5h", "5hour", "5hours", "fivehour", "five_hour", "primary"}:
        return "5h"
    if text.startswith("week") or text in {"7d", "7day", "7days", "seven_day", "secondary"}:
        return "week"
    return None


def _moment(raw: object) -> datetime | None:
    """A reset time as OpenClaw reports it: epoch milliseconds, epoch seconds, or ISO text."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        seconds = raw / 1000 if raw > 10_000_000_000 else raw
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return _parse_iso(raw)


#: How ``usage.status`` may name the ChatGPT plan: OpenClaw's ``openai`` provider, or the
#: ``openai-codex`` id it used for ChatGPT sign-ins before — a miss would never hold the agent.
_CHATGPT_USAGE_PROVIDERS = frozenset({chatgpt_auth.OPENAI_PROVIDER, "openai-codex"})

#: Polls in a row that must find no sign-in before the owner hears it is gone: one miss may be a
#: token mid-refresh or a database busy for a moment.
SIGN_IN_MISSES_TO_REPORT = 2


def chatgpt_status_from(
    *,
    signed_in: bool,
    usage: dict[str, Any] | None,
    now: datetime,
    main_model: str | None,
    specialist_model: str | None,
) -> SubscriptionStatus:
    """Turn the sign-in and OpenClaw's ``usage.status`` answer into one status.

    A window at 100 % holds the agent until it resets; with several, until the last of them does.
    """
    provider = None
    for entry in (usage or {}).get("providers") or []:
        if isinstance(entry, dict) and entry.get("provider") in _CHATGPT_USAGE_PROVIDERS:
            provider = entry
            break
    windows: list[dict[str, Any]] = []
    full_until: list[datetime] = []
    for window in (provider or {}).get("windows") or []:
        if not isinstance(window, dict):
            continue
        label = _window_label(window.get("label") or window.get("window") or window.get("name"))
        used = window.get("usedPercent", window.get("used_percent"))
        if label is None or not isinstance(used, (int, float)) or isinstance(used, bool):
            continue
        # Rounded down: 99.5 % is not used up, and holding the agent on it could last a week.
        percent = max(0, min(100, math.floor(float(used))))
        reset_at = _moment(window.get("resetAt", window.get("resetsAt", window.get("reset_at"))))
        windows.append({"window": label, "used_percent": percent, "reset_at": reset_at.isoformat() if reset_at else None})
        if float(used) >= 100 and reset_at is not None and reset_at > now:
            full_until.append(reset_at)
    plan = (provider or {}).get("plan")
    observed = _moment((usage or {}).get("updatedAt")) if provider is not None else None
    state = "connected"
    resumes_at: datetime | None = None
    if not signed_in:
        state = "signed_out"
    elif full_until:
        state, resumes_at = "limit_reached", max(full_until)
    return SubscriptionStatus(
        provider="chatgpt",
        state=state,
        observed_at=now,
        plan=str(plan) if isinstance(plan, str) and plan else None,
        main_model=main_model,
        specialist_model=specialist_model,
        windows=tuple(windows),
        resumes_at=resumes_at,
        limits_observed_at=(observed or now) if windows else None,
    )


async def gateway_usage_status() -> dict[str, Any] | None:
    """OpenClaw's ``usage.status``: plan and quota windows per provider (a loopback call)."""
    from openclaw_diagnostics.gateway import GatewayConnection, GatewayError

    try:
        async with GatewayConnection(scopes=("operator.read",)) as gateway:
            result = await gateway.call("usage.status", {}, timeout=15.0)
    except (GatewayError, OSError, TimeoutError) as exc:
        _log.info("ai_subscription_usage_unavailable", error=str(exc)[:200])
        return None
    return result if isinstance(result, dict) else None


def _bare_model(ref: str) -> str:
    return ref.split("/", 1)[1] if "/" in ref else ref


@dataclass
class AiSubscriptionMonitor:
    """Last observed subscription status for the ping; never blocks the caller."""

    data_dir: Path
    poll_seconds: float = POLL_SECONDS
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(tz=UTC))
    #: Where OpenClaw keeps its state (the ChatGPT sign-in lives in its databases).
    state_dir: Path = field(default_factory=chatgpt_auth.openclaw_state_dir)
    usage_reader: Callable[[], Awaitable[dict[str, Any] | None]] = gateway_usage_status
    _latest: SubscriptionStatus | None = field(default=None, init=False)
    _chatgpt_misses: int = field(default=0, init=False)
    _wake: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def latest_payload(self) -> dict[str, Any] | None:
        latest = self._latest
        return latest.to_ping_payload() if latest is not None else None

    def refresh_soon(self) -> None:
        """Look again right away (after a connect, a disconnect, or a turn that hit the limit)."""
        self._wake.set()

    def _models(self, choice: SubscriptionChoice) -> tuple[str | None, str | None]:
        try:
            mapping = ManifestStorage(self.data_dir).load()
            if mapping is None:
                return None, None
            overlay = resolve_overlay(bundle_manifest_from_mapping(mapping).llm.subscription, choice)
        except (OSError, ValueError, TypeError):
            return None, None
        if overlay is None:
            return None, None
        return _bare_model(overlay.supervisor.primary), _bare_model(overlay.specialist.primary)

    async def observe(self) -> SubscriptionStatus | None:
        choice = load_choice(self.data_dir)
        if choice is None:
            return None
        main_model, specialist_model = self._models(choice)
        if choice.provider == "claude":
            auth = await asyncio.to_thread(claude_cli.auth_status, self.data_dir)
            limits = read_json(claude_cli.limits_path(self.data_dir))
            return claude_status_from(
                logged_in=auth.on_subscription,
                plan=auth.subscription_type,
                limits=limits,
                now=self.clock(),
                main_model=main_model,
                specialist_model=specialist_model,
            )
        if choice.provider == "chatgpt":
            live = await asyncio.to_thread(chatgpt_auth.has_sign_in, self.state_dir)
            if live:
                self._chatgpt_misses = 0
                # Follow OpenClaw's token refreshes, so an update restores the current sign-in.
                await asyncio.to_thread(chatgpt_auth.snapshot, self.data_dir, self.state_dir)
            else:
                self._chatgpt_misses += 1
            usage = await self.usage_reader()
            signed_in = live or self._sign_in_may_still_come(gateway_answered=usage is not None)
            status = chatgpt_status_from(
                signed_in=signed_in,
                usage=usage if live else None,
                now=self.clock(),
                main_model=main_model,
                specialist_model=specialist_model,
            )
            previous = self._latest
            if signed_in and not status.windows and previous is not None and previous.provider == "chatgpt":
                # No reading this time (the gateway did not answer, or said nothing about ChatGPT):
                # keep what it last said about the limits rather than let a held agent go on it.
                still_full = previous.resumes_at is not None and previous.resumes_at > status.observed_at
                status = replace(
                    status,
                    plan=previous.plan,
                    windows=previous.windows,
                    limits_observed_at=previous.limits_observed_at,
                    state="limit_reached" if still_full else status.state,
                    resumes_at=previous.resumes_at if still_full else None,
                )
            return status
        return None

    def _sign_in_may_still_come(self, *, gateway_answered: bool) -> bool:
        """No sign-in in OpenClaw's databases now, but not lost either while a copy waits on the
        data volume and OpenClaw has not had its say: a replaced container gets it back when the
        gateway starts (``settle``), and one miss may be a token mid-refresh."""
        if not chatgpt_auth.load_snapshot(self.data_dir):
            return False
        return not gateway_answered or self._chatgpt_misses < SIGN_IN_MISSES_TO_REPORT

    async def refresh(self) -> None:
        try:
            status = await self.observe()
        except Exception as exc:  # noqa: BLE001 - a failed look keeps the last answer
            _log.warning("ai_subscription_observe_failed", error=str(exc)[:300])
            return
        self._latest = status
        if status is not None:
            try:
                atomic_write_json(status_path(self.data_dir), status.to_status_file())
            except OSError as exc:
                _log.warning("ai_subscription_status_write_failed", error=str(exc)[:300])

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._wake.clear()
            await self.refresh()
            waker = asyncio.ensure_future(self._wake.wait())
            sleeper = asyncio.ensure_future(sleep_until(stop, self.poll_seconds))
            try:
                await asyncio.wait({waker, sleeper}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (waker, sleeper):
                    task.cancel()


_monitor: AiSubscriptionMonitor | None = None


def install_ai_subscription_monitor(monitor: AiSubscriptionMonitor | None) -> None:
    global _monitor
    _monitor = monitor


def get_ai_subscription_monitor() -> AiSubscriptionMonitor | None:
    return _monitor
