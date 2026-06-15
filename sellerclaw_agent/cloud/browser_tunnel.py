from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import structlog

from sellerclaw_agent.cloud.connection_client import SellerClawConnectionClient
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url

_log = structlog.get_logger(__name__)

# Cloud close code meaning "session is over, do not redial" (mirrors the cloud's
# TUNNEL_CLOSE_SESSION_ENDED in src/agent/browser_view/infra/registry.py).
_CLOSE_SESSION_ENDED = 4001
# Denial statuses that also mean "stop": auth broken or session gone.
_STOP_STATUSES = frozenset({401, 404, 410})

_ATTACH_MARKER = "attach"

_REDIAL_BACKOFF_INITIAL_SECONDS = 1.0
_REDIAL_BACKOFF_MAX_SECONDS = 15.0
# How long a dialed-but-idle tunnel waits for a viewer before returning to the
# redial loop, which re-validates the session. Bounds how long an orphaned
# tunnel lingers after the user closes the modal (session deleted on the cloud).
_VIEWER_PARK_TIMEOUT_SECONDS = 30.0


class _SessionEnded(Exception):
    """Tunnel must stop redialing (session gone / explicitly ended)."""


def _cloud_tunnel_url(session_id: UUID) -> str:
    parsed = urlsplit(get_sellerclaw_api_url().rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"{parsed.path}/agent/browser-view/tunnel"
    return urlunsplit((scheme, parsed.netloc, path, f"session_id={session_id}", ""))


def _local_vnc_url() -> str:
    return os.environ.get("OPENCLAW_BROWSER_VNC_WS_URL", "ws://127.0.0.1:6080/websockify")


def _local_vnc_headers() -> dict[str, str]:
    # KasmVNC enforces HTTP Basic auth on its websocket (user/VNC_PASSWORD from
    # kasmvnc_start); the viewer's browser cannot send these — we inject them here.
    user = os.environ.get("KASMVNC_USER", "user")
    password = os.environ.get("VNC_PASSWORD", "password")
    encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


async def _pump_ws(src, dst) -> None:
    async for message in src:
        await dst.send(message)


async def _serve_one_tunnel(*, session_id: UUID, bearer_token: str) -> None:
    """Dial the cloud, wait for a viewer to attach, then pipe KasmVNC bytes.

    Returns normally when the viewer detached (caller redials); raises
    ``_SessionEnded`` when the cloud says the session is over.
    """
    import websockets
    from websockets.exceptions import ConnectionClosed, InvalidStatus

    try:
        async with websockets.connect(
            _cloud_tunnel_url(session_id),
            additional_headers={"Authorization": f"Bearer {bearer_token}"},
            max_size=None,
        ) as cloud_ws:
            _log.info("browser_tunnel_connected", session_id=str(session_id))
            # Pre-attach control phase: the local VNC socket is opened only once a
            # viewer is attached so the RFB handshake reaches it from byte 0. The
            # recv is bounded so an idle tunnel periodically returns to the redial
            # loop, which re-validates the session (and stops once it's deleted).
            attached = False
            while not attached:
                try:
                    message = await asyncio.wait_for(
                        cloud_ws.recv(), timeout=_VIEWER_PARK_TIMEOUT_SECONDS
                    )
                except TimeoutError:
                    return
                except ConnectionClosed as exc:
                    if exc.rcvd is not None and exc.rcvd.code == _CLOSE_SESSION_ENDED:
                        raise _SessionEnded from exc
                    return
                if isinstance(message, str) and message == _ATTACH_MARKER:
                    attached = True

            _log.info("browser_tunnel_viewer_attached", session_id=str(session_id))
            async with websockets.connect(
                _local_vnc_url(),
                additional_headers=_local_vnc_headers(),
                # KasmVNC requires an Origin header on the upgrade and never answers
                # websocket pings (it would kill the link after the ping timeout).
                origin=websockets.Origin("http://127.0.0.1:6080"),
                subprotocols=[websockets.Subprotocol("binary")],
                ping_interval=None,
                max_size=None,
            ) as local_ws:
                pumps = {
                    asyncio.create_task(_pump_ws(cloud_ws, local_ws)),
                    asyncio.create_task(_pump_ws(local_ws, cloud_ws)),
                }
                try:
                    done, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        error = task.exception()
                        if error is not None and not isinstance(error, ConnectionClosed):
                            raise error
                finally:
                    _log.info("browser_tunnel_viewer_detached", session_id=str(session_id))
            # Inspect how the cloud leg ended: explicit end-of-session code → stop.
            close = getattr(cloud_ws.protocol, "close_rcvd", None)
            if close is not None and close.code == _CLOSE_SESSION_ENDED:
                raise _SessionEnded
    except InvalidStatus as exc:
        if exc.response.status_code in _STOP_STATUSES:
            _log.info(
                "browser_tunnel_rejected_stop",
                session_id=str(session_id),
                status=exc.response.status_code,
            )
            raise _SessionEnded from exc
        raise


async def run_browser_view_tunnel(
    *,
    session_id: UUID,
    client: SellerClawConnectionClient,
) -> None:
    """Keep one tunnel available for the session: dial, serve, redial until it ends."""
    backoff = _REDIAL_BACKOFF_INITIAL_SECONDS
    while True:
        active = await _fetch_active_session_id(client)
        if active != session_id:
            _log.info(
                "browser_tunnel_session_gone",
                session_id=str(session_id),
                active=str(active) if active else None,
            )
            return
        try:
            token = client.bearer_token()
        except Exception as exc:  # noqa: BLE001 - credentials missing/unreadable
            _log.warning("browser_tunnel_no_token", session_id=str(session_id), error=str(exc)[:200])
            return
        try:
            await _serve_one_tunnel(session_id=session_id, bearer_token=token)
            backoff = _REDIAL_BACKOFF_INITIAL_SECONDS
            # Viewer detached / cloud recycled the socket — redial quickly so a
            # modal reopen attaches without a new command round-trip.
            await asyncio.sleep(0.5)
        except _SessionEnded:
            _log.info("browser_tunnel_ended", session_id=str(session_id))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep redialing on transient errors
            _log.warning(
                "browser_tunnel_error",
                session_id=str(session_id),
                error=str(exc)[:300],
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _REDIAL_BACKOFF_MAX_SECONDS)


async def _fetch_active_session_id(client: SellerClawConnectionClient) -> UUID | None:
    try:
        return await client.fetch_browser_view_session_id()
    except Exception as exc:  # noqa: BLE001 - treat cloud hiccups as "still active"
        _log.warning("browser_tunnel_session_check_failed", error=str(exc)[:300])
        return None


@dataclass
class BrowserViewTunnelManager:
    """At most one tunnel task per agent process (sessions are per-user singletons)."""

    _task: asyncio.Task[None] | None = None
    _session_id: UUID | None = None

    def start(self, *, session_id: UUID, client: SellerClawConnectionClient) -> None:
        if self._task is not None and not self._task.done() and self._session_id == session_id:
            return  # idempotent re-delivery of the same command
        self.stop()
        self._session_id = session_id
        self._task = asyncio.create_task(
            run_browser_view_tunnel(session_id=session_id, client=client),
            name="browser_view_tunnel",
        )

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._session_id = None

    @property
    def running_session_id(self) -> UUID | None:
        if self._task is None or self._task.done():
            return None
        return self._session_id


_manager = BrowserViewTunnelManager()


def get_browser_tunnel_manager() -> BrowserViewTunnelManager:
    return _manager
