"""Link the assistant's WhatsApp number from the SellerClaw website.

The owner opens the link step on the website; the cloud sends the payload-less
``whatsapp_link`` command, the command handler fetches the attempt (``login_id``, ``force``)
and starts this runner in the background, then acks at once — a link takes minutes and the
cloud times out an unacknowledged command after one.

The runner drives the gateway's QR login, the same RPC pair OpenClaw's own Control UI uses:

* ``web.login.start`` returns the first QR as a PNG data URL (or says the account is already
  linked). With ``force`` the plugin logs the current session out first — "link another phone".
* ``web.login.wait`` long-polls: it returns when the code is scanned, when WhatsApp rotates the
  QR (every ~20 s — passing ``currentQrDataUrl`` makes it return the new image), or on its own
  timeout with "Still waiting". The plugin keeps a login for 180 s.

Every state is POSTed to the cloud, which the website polls. A 404 from the cloud means the
owner closed or replaced the attempt, and the runner stops. The channel plugin only exists
once the manifest that enabled WhatsApp has been applied — and OpenClaw defers a channel
reload until running agent work finishes — so the runner first waits for the plugin, reporting
``preparing`` meanwhile.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Protocol
from uuid import UUID

import structlog
from openclaw_diagnostics.gateway import GatewayError, GatewayUnreachableError

from sellerclaw_agent.cloud.whatsapp_channel import (
    WHATSAPP_CHANNEL,
    GatewayFactory,
    default_gateway_factory,
    read_whatsapp_status,
)

_log = structlog.get_logger(__name__)

PLUGIN_WAIT_SECONDS: Final[float] = 300.0
PLUGIN_POLL_SECONDS: Final[float] = 3.0
LOGIN_DEADLINE_SECONDS: Final[float] = 180.0
START_QR_TIMEOUT_MS: Final[int] = 30_000
START_CALL_TIMEOUT_S: Final[float] = 45.0
WAIT_TIMEOUT_MS: Final[int] = 20_000
WAIT_CALL_TIMEOUT_S: Final[float] = 30.0
SELF_NUMBER_ATTEMPTS: Final[int] = 5
SELF_NUMBER_RETRY_SECONDS: Final[float] = 2.0
UNSTABLE_AUTH_RETRIES: Final[int] = 5
UNSTABLE_AUTH_RETRY_SECONDS: Final[float] = 2.0
WAIT_ERROR_RETRY_SECONDS: Final[float] = 2.0
FINAL_REPORT_ATTEMPTS: Final[int] = 5
FINAL_REPORT_RETRY_SECONDS: Final[float] = 3.0

# Plugin wording (extensions/whatsapp login-qr.ts) for the outcomes that are not failures.
_ALREADY_LINKED_PREFIX: Final[str] = "whatsapp is already linked"
_STILL_WAITING_PREFIX: Final[str] = "still waiting"
_EXPIRED_MARKER: Final[str] = "qr expired"
_REPLACED_MARKERS: Final[tuple[str, ...]] = (
    "no active whatsapp login",
    "replaced by a newer request",
)
_UNSTABLE_AUTH_CODE: Final[str] = "WHATSAPP_AUTH_UNSTABLE"
_TIMED_OUT_MARKER: Final[str] = "timed out after"  # GatewayConnection.call() on its own timeout


class LinkStatus(StrEnum):
    PREPARING = "preparing"
    QR = "qr"
    CONNECTED = "connected"
    FAILED = "failed"
    EXPIRED = "expired"


class LinkFailure(StrEnum):
    """Why a link attempt failed, for the website to word it."""

    ASSISTANT_NOT_RUNNING = "assistant_not_running"
    CHANNEL_NOT_READY = "channel_not_ready"
    REPLACED = "replaced"
    LOGIN_FAILED = "login_failed"


@dataclass(frozen=True)
class LinkState:
    status: LinkStatus
    qr_data_url: str | None = None
    reason: LinkFailure | None = None
    message: str | None = None
    self_e164: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "qr_data_url": self.qr_data_url,
            "reason": self.reason.value if self.reason is not None else None,
            "message": (self.message or "")[:500] or None,
            "self_e164": self.self_e164,
        }


class WhatsappLinkCloud(Protocol):
    async def post_whatsapp_link_state(self, *, login_id: UUID, state: dict[str, Any]) -> bool: ...


class _AttemptClosedError(Exception):
    """The cloud no longer knows this attempt — stop quietly."""


@dataclass
class WhatsappLinkRunner:
    login_id: UUID
    force: bool
    cloud: WhatsappLinkCloud
    gateway_factory: GatewayFactory = default_gateway_factory
    on_linked: Callable[[], None] | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    plugin_wait_seconds: float = PLUGIN_WAIT_SECONDS
    login_deadline_seconds: float = LOGIN_DEADLINE_SECONDS
    _qr: str | None = field(default=None, init=False)

    async def run(self) -> LinkStatus:
        try:
            final = await self._run()
        except _AttemptClosedError:
            _log.info("whatsapp_link_closed_by_cloud", login_id=str(self.login_id))
            return LinkStatus.FAILED
        _log.info("whatsapp_link_finished", login_id=str(self.login_id), status=final.status.value)
        return final.status

    async def _report(self, state: LinkState) -> bool:
        """POST one state; False when it did not get through (a 404 raises instead)."""
        try:
            accepted = await self.cloud.post_whatsapp_link_state(
                login_id=self.login_id,
                state=state.to_payload(),
            )
        except Exception as exc:  # noqa: BLE001 - a lost QR step is replaced by the next one
            _log.warning("whatsapp_link_report_failed", login_id=str(self.login_id), error=str(exc)[:300])
            return False
        if not accepted:
            raise _AttemptClosedError
        return True

    async def _finish(self, state: LinkState) -> LinkState:
        """Report the outcome, retrying: nothing comes after it to take its place, and a lost
        "connected" would leave the website showing a used code until the attempt expires."""
        for attempt in range(FINAL_REPORT_ATTEMPTS):
            if await self._report(state):
                break
            if attempt + 1 < FINAL_REPORT_ATTEMPTS:
                await self.sleep(FINAL_REPORT_RETRY_SECONDS * (attempt + 1))
        return state

    async def _call(self, method: str, params: dict[str, Any], *, timeout: float) -> Any:
        async with self.gateway_factory() as conn:
            return await conn.call(method, params, timeout=timeout)

    async def _run(self) -> LinkState:
        await self._report(LinkState(status=LinkStatus.PREPARING))
        ready = await self._wait_for_plugin()
        if ready is not None:
            return await self._finish(ready)
        try:
            start = await self._start_login()
        except GatewayUnreachableError as exc:
            return await self._finish(self._failed(LinkFailure.ASSISTANT_NOT_RUNNING, exc))
        except GatewayError as exc:
            return await self._finish(self._failed(LinkFailure.LOGIN_FAILED, exc))
        if start is not None:
            return await self._finish(start)
        return await self._finish(await self._wait_for_scan())

    @staticmethod
    def _failed(reason: LinkFailure, detail: object) -> LinkState:
        return LinkState(status=LinkStatus.FAILED, reason=reason, message=str(detail)[:500])

    async def _wait_for_plugin(self) -> LinkState | None:
        """Wait until the WhatsApp plugin answers; a terminal state when it never does."""
        deadline = self.clock() + self.plugin_wait_seconds
        while True:
            try:
                status = await read_whatsapp_status(self.gateway_factory)
            except GatewayUnreachableError as exc:
                return self._failed(LinkFailure.ASSISTANT_NOT_RUNNING, exc)
            except GatewayError as exc:
                # Busy gateway (a long tool call holds the event loop) — keep waiting.
                _log.info("whatsapp_link_status_retry", error=str(exc)[:200])
                status = None
            if status is not None:
                return None
            if self.clock() >= deadline:
                return self._failed(
                    LinkFailure.CHANNEL_NOT_READY,
                    "The WhatsApp channel did not load on the assistant.",
                )
            await self.sleep(PLUGIN_POLL_SECONDS)

    async def _start_login(self) -> LinkState | None:
        """Run ``web.login.start``. Returns a terminal state, or None once a QR is showing."""
        for attempt in range(UNSTABLE_AUTH_RETRIES + 1):
            result = await self._call(
                "web.login.start",
                {"channel": WHATSAPP_CHANNEL, "force": self.force, "timeoutMs": START_QR_TIMEOUT_MS},
                timeout=START_CALL_TIMEOUT_S,
            )
            if not isinstance(result, dict):
                return self._failed(LinkFailure.LOGIN_FAILED, f"unexpected response: {result!r}"[:300])
            if result.get("code") == _UNSTABLE_AUTH_CODE and attempt < UNSTABLE_AUTH_RETRIES:
                await self.sleep(UNSTABLE_AUTH_RETRY_SECONDS)
                continue
            qr = result.get("qrDataUrl")
            if isinstance(qr, str) and qr:
                self._qr = qr
                await self._report(LinkState(status=LinkStatus.QR, qr_data_url=qr))
                return None
            message = str(result.get("message") or "")
            if result.get("connected") is True or message.lower().startswith(_ALREADY_LINKED_PREFIX):
                return await self._connected()
            return self._failed(LinkFailure.LOGIN_FAILED, message or "WhatsApp login did not start.")
        return self._failed(LinkFailure.LOGIN_FAILED, "WhatsApp auth state did not settle.")

    async def _wait_for_scan(self) -> LinkState:
        deadline = self.clock() + self.login_deadline_seconds
        while self.clock() < deadline:
            try:
                result = await self._call(
                    "web.login.wait",
                    {
                        "channel": WHATSAPP_CHANNEL,
                        "timeoutMs": WAIT_TIMEOUT_MS,
                        "currentQrDataUrl": self._qr,
                    },
                    timeout=WAIT_CALL_TIMEOUT_S,
                )
            except GatewayUnreachableError as exc:
                return self._failed(LinkFailure.ASSISTANT_NOT_RUNNING, exc)
            except GatewayError as exc:
                if _TIMED_OUT_MARKER not in str(exc):
                    return self._failed(LinkFailure.LOGIN_FAILED, exc)
                # A busy gateway (a long tool call) times the wait out; the plugin still holds
                # the code, so keep waiting until the deadline says otherwise.
                _log.info("whatsapp_link_wait_retry", error=str(exc)[:200])
                await self.sleep(WAIT_ERROR_RETRY_SECONDS)
                continue
            if not isinstance(result, dict):
                return self._failed(LinkFailure.LOGIN_FAILED, f"unexpected response: {result!r}"[:300])
            if result.get("connected") is True:
                return await self._connected()
            qr = result.get("qrDataUrl")
            if isinstance(qr, str) and qr:
                self._qr = qr
                await self._report(LinkState(status=LinkStatus.QR, qr_data_url=qr))
                continue
            message = str(result.get("message") or "")
            lowered = message.lower()
            if lowered.startswith(_STILL_WAITING_PREFIX):
                continue
            if _EXPIRED_MARKER in lowered:
                return LinkState(status=LinkStatus.EXPIRED, message=message)
            if any(marker in lowered for marker in _REPLACED_MARKERS):
                return self._failed(LinkFailure.REPLACED, message)
            return self._failed(LinkFailure.LOGIN_FAILED, message or "WhatsApp login ended.")
        return LinkState(status=LinkStatus.EXPIRED)

    async def _connected(self) -> LinkState:
        """The account is linked: learn its number, ask for a session copy, report."""
        self_e164: str | None = None
        for _ in range(SELF_NUMBER_ATTEMPTS):
            try:
                status = await read_whatsapp_status(self.gateway_factory)
            except GatewayError:
                status = None
            if status is not None and status.self_e164:
                self_e164 = status.self_e164
                break
            await self.sleep(SELF_NUMBER_RETRY_SECONDS)
        if self.on_linked is not None:
            self.on_linked()
        return LinkState(status=LinkStatus.CONNECTED, self_e164=self_e164)


@dataclass
class WhatsappLinkManager:
    """At most one link runner per agent process (one attempt per owner at a time)."""

    _task: asyncio.Task[LinkStatus] | None = None
    _login_id: UUID | None = None

    def start(self, runner: WhatsappLinkRunner) -> None:
        if self._task is not None and not self._task.done() and self._login_id == runner.login_id:
            return  # the same command delivered again
        self.stop()
        self._login_id = runner.login_id
        self._task = asyncio.create_task(runner.run(), name="whatsapp_link")

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._login_id = None

    @property
    def running_login_id(self) -> UUID | None:
        if self._task is None or self._task.done():
            return None
        return self._login_id


_manager = WhatsappLinkManager()


def get_whatsapp_link_manager() -> WhatsappLinkManager:
    return _manager
