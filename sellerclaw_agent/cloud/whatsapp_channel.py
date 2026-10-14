"""WhatsApp channel state as the gateway reports it, for the link runner and the ping.

The channel is OpenClaw's official ``@openclaw/whatsapp`` plugin (vendored into the image). Its
state comes from the gateway's ``channels.status`` RPC: the plugin summary carries whether a
WhatsApp account is linked, which number it is, whether the socket is up, and ``healthState``
— ``logged-out`` once the phone revoked the linked device.

Reading it costs one loopback WebSocket round-trip, but the gateway is single-threaded and
stalls during heavy tool calls, so nothing on the ping path waits for it: a background monitor
refreshes a cached value and the ping sends whatever was last observed, stamped with the time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import structlog
from openclaw_diagnostics.gateway import GatewayConnection, GatewayError, GatewayUnreachableError

from sellerclaw_agent.async_backoff import sleep_until

_log = structlog.get_logger(__name__)

WHATSAPP_CHANNEL: Final[str] = "whatsapp"
# The gateway refuses a status/login call for a channel no loaded plugin owns with this text.
_UNKNOWN_CHANNEL_MARKER: Final[str] = "unknown channel"

STATUS_CALL_TIMEOUT_S: Final[float] = 3.0
STATUS_POLL_SECONDS: Final[float] = 30.0

# web.login.* and channels.logout are operator.admin methods; status only needs read.
ADMIN_SCOPES: Final[tuple[str, ...]] = ("operator.read", "operator.admin")


class WhatsappGatewayConnection(Protocol):
    """The slice of an open gateway connection the WhatsApp code uses."""

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any: ...


GatewayFactory = Callable[[], AbstractAsyncContextManager[WhatsappGatewayConnection]]


def default_gateway_factory() -> AbstractAsyncContextManager[WhatsappGatewayConnection]:
    return GatewayConnection(scopes=ADMIN_SCOPES)


def is_unknown_channel_error(exc: GatewayError) -> bool:
    """The WhatsApp plugin is not loaded (channel not configured yet, or still reloading)."""
    return _UNKNOWN_CHANNEL_MARKER in str(exc).lower()


@dataclass(frozen=True)
class WhatsappChannelStatus:
    """What the plugin reports about the (default) WhatsApp account."""

    linked: bool | None
    connected: bool
    self_e164: str | None
    logged_out: bool
    observed_at: datetime

    def to_ping_payload(self) -> dict[str, Any]:
        return {
            "linked": self.linked,
            "connected": self.connected,
            "self_e164": self.self_e164,
            "logged_out": self.logged_out,
            "observed_at": self.observed_at.isoformat(),
        }


def parse_whatsapp_status(payload: Any, *, observed_at: datetime) -> WhatsappChannelStatus | None:
    """Pick the WhatsApp summary out of a ``channels.status`` payload (None if it is absent)."""
    if not isinstance(payload, dict):
        return None
    channels = payload.get("channels")
    summary = channels.get(WHATSAPP_CHANNEL) if isinstance(channels, dict) else None
    if not isinstance(summary, dict):
        return None
    linked_raw = summary.get("linked")
    linked = linked_raw if isinstance(linked_raw, bool) else None
    self_block = summary.get("self")
    e164 = self_block.get("e164") if isinstance(self_block, dict) else None
    logged_out = summary.get("healthState") == "logged-out"
    return WhatsappChannelStatus(
        linked=False if logged_out else linked,
        connected=summary.get("connected") is True,
        self_e164=e164.strip() if isinstance(e164, str) and e164.strip() else None,
        logged_out=logged_out,
        observed_at=observed_at,
    )


async def read_whatsapp_status(
    factory: GatewayFactory,
    *,
    timeout: float = STATUS_CALL_TIMEOUT_S,
) -> WhatsappChannelStatus | None:
    """Current channel status, or None when the plugin is not loaded.

    Raises ``GatewayUnreachableError`` when OpenClaw is not running and ``GatewayError`` for
    any other refusal — callers decide whether that is "unknown" or a failure.
    """
    async with factory() as conn:
        try:
            payload = await conn.call(
                "channels.status",
                {"channel": WHATSAPP_CHANNEL},
                timeout=timeout,
            )
        except GatewayError as exc:
            if isinstance(exc, GatewayUnreachableError) or not is_unknown_channel_error(exc):
                raise
            return None
    return parse_whatsapp_status(payload, observed_at=datetime.now(UTC))


class WhatsappStatusMonitor:
    """Keeps the last observed channel status for the ping; never blocks the caller."""

    def __init__(
        self,
        *,
        factory: GatewayFactory = default_gateway_factory,
        poll_seconds: float = STATUS_POLL_SECONDS,
    ) -> None:
        self._factory = factory
        self._poll_seconds = poll_seconds
        self._latest: WhatsappChannelStatus | None = None
        self._wake = asyncio.Event()

    def latest_payload(self) -> dict[str, Any] | None:
        latest = self._latest
        return latest.to_ping_payload() if latest is not None else None

    def refresh_soon(self) -> None:
        """Re-read on the next tick (after a link or logout) instead of waiting the full period."""
        self._wake.set()

    async def refresh(self) -> None:
        try:
            self._latest = await read_whatsapp_status(self._factory)
        except GatewayUnreachableError:
            # OpenClaw stopped: what we knew about the link is no longer current.
            self._latest = None
        except GatewayError as exc:
            # A busy gateway (long tool call) times out here; keep the last value, it is
            # stamped with when it was seen.
            _log.debug("whatsapp_status_read_failed", error=str(exc)[:300])

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            # Cleared before reading, so a refresh_soon() that lands during the read still counts.
            self._wake.clear()
            await self.refresh()
            waker = asyncio.ensure_future(self._wake.wait())
            sleeper = asyncio.ensure_future(sleep_until(stop, self._poll_seconds))
            try:
                await asyncio.wait({waker, sleeper}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (waker, sleeper):
                    task.cancel()


_monitor = WhatsappStatusMonitor()


def get_whatsapp_status_monitor() -> WhatsappStatusMonitor:
    return _monitor
