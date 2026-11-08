"""Background task: SellerClaw hooks SSE → local OpenClaw /hooks/agent."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import structlog
from openclaw_diagnostics.gateway import GatewayError

from sellerclaw_agent.async_backoff import (
    is_overload_status,
    ping_interval_when_suspended,
    sleep_until,
    sse_backoff_ceiling,
    sse_clean_reconnect_sleep,
    sse_reconnect_sleep,
)
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.edge_sse_common import OpenClawGate, make_openclaw_gate, raise_for_edge_sse_status
from sellerclaw_agent.cloud.exceptions import (
    CloudAgentSuspendedError,
    CloudAuthError,
    CloudConnectionError,
    CloudConnectionInactiveError,
    CloudSessionInvalidatedError,
)
from sellerclaw_agent.cloud.messenger_send import default_send_gateway_factory, send_text
from sellerclaw_agent.cloud.openclaw_forwarder import (
    INBOUND_FORWARD_TIMEOUT,
    LocalOpenClawForwarder,
    openclaw_gateway_base_url,
)
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url
from sellerclaw_agent.cloud.sse_codec import iter_sse_events
from sellerclaw_agent.cloud.whatsapp_channel import GatewayFactory
from sellerclaw_agent.http_clients import async_client
from sellerclaw_agent.server.secrets_store import get_secrets

_log = structlog.get_logger(__name__)

# Cloud sends a heartbeat every 15s on ``/agent/hooks/stream``. A tight read timeout
# (~4× heartbeat) makes silently-dead TCP connections observable; the outer loop
# will fall into its normal backoff+reconnect path.
_SSE_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)

# Short on purpose: the report is bookkeeping about a hook that has already been handled, and the
# cloud re-sends what it never hears about anyway.
_DELIVERY_REPORT_TIMEOUT = httpx.Timeout(15.0)

#: What the cloud may address through a hook event: ``agent`` is ``/hooks/agent`` (an agent turn),
#: ``send`` is the gateway's plain send RPC (a ready text to a messenger, no turn — see
#: :mod:`sellerclaw_agent.cloud.messenger_send`). The gateway's event queue (``/hooks/wake``) was
#: tried for background nudges and dropped — a queued event runs only once the session goes idle,
#: and it is accepted with a 200 either way, so a hook could sit unrun for minutes while the report
#: below called it delivered.
_HOOK_ENDPOINTS = frozenset({"agent", "send", "steer"})


async def report_hook_delivery(
    *, agent_token: str, hook_id: str, delivered: bool, error: str | None
) -> None:
    """Tell the cloud whether this hook actually reached the local gateway.

    The cloud can only see that a live edge subscriber took the payload off its stream, and that
    is not the same as the gateway accepting it: a run refused admission, a gateway that is down,
    a hook forwarded while OpenClaw was restarting. Without this the cloud counts all of those as
    delivered and the updates they carried are never shown again.

    Best-effort by design. A cloud too old to know this endpoint answers 404, and a report that
    does not arrive leaves the cloud exactly where it was before this existed — so nothing here is
    allowed to interfere with the forwarding it describes.
    """
    base = get_sellerclaw_api_url().rstrip("/")
    try:
        async with async_client(timeout=_DELIVERY_REPORT_TIMEOUT) as client:
            response = await client.post(
                f"{base}/agent/hooks/delivery",
                headers={"Authorization": f"Bearer {agent_token}"},
                json={"hook_id": hook_id, "delivered": delivered, "error": error},
            )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - reporting must never break forwarding
        _log.info("hook_delivery_report_failed", hook_id=hook_id, error=str(exc))


async def forward_hook_event(
    payload: dict[str, Any],
    *,
    forwarder: LocalOpenClawForwarder,
    openclaw_gate: OpenClawGate,
    agent_token: str | None = None,
    send_gateway: GatewayFactory = default_send_gateway_factory,
) -> None:
    """Forward a ``hook_event`` to the local OpenClaw gateway (gated on readiness).

    Two shapes arrive here. Most senders publish the gateway body directly and it goes to
    ``/hooks/agent`` — that is every hook there has ever been. A sender that needs more says so in
    an envelope (``endpoint`` / ``body`` / ``hookId``): a name for the hook so what became of it
    can be reported back — including the gateway refusing a run it could not admit in time,
    which is the one outcome the cloud cannot see for itself. The cloud only sends envelopes to
    agents whose protocol version says they are understood, so an unknown ``endpoint`` here is a
    bug rather than an old peer — it is refused and reported rather than guessed at.
    """
    endpoint, body, hook_id = _read_hook_envelope(payload)
    tracked = agent_token is not None and bool(hook_id)

    async def _report(delivered: bool, error: str | None) -> None:
        if tracked and agent_token is not None and hook_id is not None:
            await report_hook_delivery(
                agent_token=agent_token, hook_id=hook_id, delivered=delivered, error=error
            )

    if endpoint not in _HOOK_ENDPOINTS:
        _log.warning("hook_event_dropped", reason="unknown_endpoint", endpoint=endpoint)
        await _report(False, f"unknown_endpoint: {endpoint}")
        return

    running, oc_status, oc_err = await openclaw_gate()
    if not running:
        _log.info(
            "hook_event_dropped",
            reason="openclaw_not_running",
            openclaw_status=oc_status,
            openclaw_error=oc_err,
        )
        await _report(False, f"openclaw_not_running status={oc_status} error={oc_err}")
        return
    if endpoint == "send":
        try:
            await send_text(body, gateway_factory=send_gateway)
        except (GatewayError, ValueError) as exc:
            _log.warning("messenger_send_failed", channel=body.get("channel"), error=str(exc)[:500])
            await _report(False, str(exc)[:500])
        except Exception as exc:  # noqa: BLE001 - the outcome is reported, then left in the log
            _log.exception("messenger_send_crashed", channel=body.get("channel"))
            await _report(False, str(exc)[:500])
        else:
            _log.info("messenger_send_ok", channel=body.get("channel"))
            await _report(True, None)
        return
    if endpoint == "steer":
        await _steer_active_run(body, forwarder=forwarder, report=_report)
        return
    try:
        await forwarder.post_hooks_agent_json(body)
    except httpx.ConnectError as exc:
        _log.info("hook_event_dropped", reason="gateway_unreachable", error=str(exc))
        await _report(False, f"gateway_unreachable: {exc}")
    except httpx.TimeoutException as exc:
        _log.info("hook_event_dropped", reason="gateway_timeout", error=str(exc))
        await _report(False, f"gateway_timeout: {exc}")
    except Exception as exc:  # noqa: BLE001 - the outcome is reported, then left in the log
        _log.exception("hooks_forward_failed")
        await _report(False, str(exc)[:500])
    else:
        await _report(True, None)


async def _steer_active_run(
    body: dict[str, Any],
    *,
    forwarder: LocalOpenClawForwarder,
    report: Callable[[bool, str | None], Awaitable[None]],
) -> None:
    """A ``steer`` envelope: hand the text to the session's active run, report whether it took.

    The cloud sends one only for a session that is mid-run, which must never be woken with a hook
    (the gateway's refusal discards the reply being prepared). So nothing here falls back to a
    wake: a run that did not take the text — or a gateway that failed to answer — is reported as
    not delivered, and the cloud keeps the update for its next sweep.
    """
    session_key = body.get("sessionKey")
    try:
        outcome = await forwarder.post_steer_json(body)
    except (httpx.HTTPError, ValueError) as exc:
        _log.info("hook_steer_failed", session_key=session_key, error=str(exc)[:500])
        await report(False, f"steer_failed: {str(exc)[:500]}")
        return
    except Exception as exc:  # noqa: BLE001 - the outcome is reported, then left in the log
        _log.exception("hook_steer_crashed", session_key=session_key)
        await report(False, str(exc)[:500])
        return
    steered = outcome.get("steered") is True
    reason = None if steered else str(outcome.get("reason") or "not steered")[:500]
    _log.info("hook_steer_done", session_key=session_key, steered=steered, reason=reason)
    await report(steered, reason)


def _read_hook_envelope(payload: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
    """Split an incoming hook into (endpoint, gateway body, hook id).

    A payload without an ``endpoint`` key is the plain gateway body every sender has always used;
    it goes to ``/hooks/agent`` unnamed, exactly as before. ``endpoint`` is never a valid field of
    a gateway body itself, so there is nothing to collide with.
    """
    raw_endpoint = payload.get("endpoint")
    if not isinstance(raw_endpoint, str):
        return "agent", payload, None
    body = payload.get("body")
    hook_id = payload.get("hookId")
    return (
        raw_endpoint,
        body if isinstance(body, dict) else {},
        hook_id if isinstance(hook_id, str) and hook_id else None,
    )


async def _consume_hooks_sse(
    *,
    agent_token: str,
    agent_instance_id: UUID,
    forwarder: LocalOpenClawForwarder,
    openclaw_gate: OpenClawGate,
    stop: asyncio.Event,
) -> None:
    base = get_sellerclaw_api_url().rstrip("/")
    url = f"{base}/agent/hooks/stream"
    params = {"agent_instance_id": str(agent_instance_id)}
    headers = {"Authorization": f"Bearer {agent_token}"}
    async with async_client(timeout=_SSE_TIMEOUT) as client:
        async with client.stream("GET", url, headers=headers, params=params) as response:
            await raise_for_edge_sse_status(response, label="hooks_sse")
            async for event_name, data in iter_sse_events(response):
                if stop.is_set():
                    break
                if event_name == "heartbeat":
                    continue
                if event_name == "error":
                    _log.warning("hooks_sse_error_event", data_preview=data[:300])
                    continue
                if event_name != "hook_event":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    _log.warning("hooks_sse_invalid_json")
                    continue
                if not isinstance(payload, dict):
                    continue
                await forward_hook_event(
                    payload,
                    forwarder=forwarder,
                    openclaw_gate=openclaw_gate,
                    agent_token=agent_token,
                )


async def run_edge_hooks_sse_loop(
    stop: asyncio.Event,
    *,
    registry: Any | None = None,
    supervisor_probe: Any | None = None,
) -> None:
    """Long-lived loop: cloud hooks SSE → OpenClaw ``/hooks/agent``."""
    from sellerclaw_agent.cloud.supervisor_manager import (  # noqa: PLC0415
        SupervisorContainerManager,
        create_supervisor_manager,
    )

    data_dir = Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    creds_storage = CredentialsStorage(data_dir)
    session_storage = EdgeSessionStorage(data_dir)
    supervisor_mgr: SupervisorContainerManager = supervisor_probe or create_supervisor_manager()
    _openclaw_gate = make_openclaw_gate(supervisor_mgr)

    backoff = 2.0
    while not stop.is_set():
        bearer = resolve_agent_bearer_token(creds_storage)
        if bearer is None:
            await sleep_until(stop, 10.0)
            backoff = 2.0
            continue
        sess = session_storage.load()
        if sess is None:
            await sleep_until(stop, 5.0)
            continue
        try:
            async with async_client(timeout=INBOUND_FORWARD_TIMEOUT) as oc_http:
                secrets = get_secrets(data_dir)
                forwarder = LocalOpenClawForwarder(
                    base_url=openclaw_gateway_base_url(),
                    hooks_token=secrets.hooks_token,
                    gateway_token=secrets.gateway_token,
                    http_client=oc_http,
                )
                if registry is not None:
                    registry.mark_hooks_sse_connected(True)
                try:
                    await _consume_hooks_sse(
                        agent_token=bearer,
                        agent_instance_id=sess.agent_instance_id,
                        forwarder=forwarder,
                        openclaw_gate=_openclaw_gate,
                        stop=stop,
                    )
                finally:
                    if registry is not None:
                        registry.mark_hooks_sse_connected(False)
                # Clean close usually means the cloud restarted/redeployed and dropped every
                # agent at once — jitter the reconnect so the fleet doesn't stampede.
                await sleep_until(stop, sse_clean_reconnect_sleep())
                backoff = 2.0
        except CloudSessionInvalidatedError as exc:
            _log.warning("hooks_sse_session_invalidated_clearing_session", error=str(exc))
            session_storage.clear()
            await sleep_until(stop, 2.0)
            backoff = 2.0
            continue
        except CloudAuthError as exc:
            _log.warning("hooks_sse_unauthorized_clearing_session", error=str(exc))
            session_storage.clear()
            await sleep_until(stop, 10.0)
            backoff = 2.0
            continue
        except CloudAgentSuspendedError as exc:
            _log.warning("hooks_sse_agent_suspended_backing_off", error=str(exc))
            await sleep_until(stop, ping_interval_when_suspended())
            backoff = 2.0
            continue
        except CloudConnectionInactiveError as exc:
            _log.info("hooks_sse_connection_inactive_retrying", error=str(exc))
            await sleep_until(stop, 5.0)
            backoff = 2.0
            continue
        except CloudConnectionError as exc:
            if str(exc) == "hooks_sse_forbidden":
                _log.warning("hooks_sse_forbidden_backing_off")
                await sleep_until(stop, ping_interval_when_suspended())
                backoff = 2.0
                continue
            _log.warning("hooks_sse_stopped", error=str(exc))
            await sleep_until(stop, sse_reconnect_sleep(backoff))
            backoff = sse_backoff_ceiling(backoff)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            overloaded = isinstance(exc, httpx.HTTPStatusError) and is_overload_status(
                exc.response.status_code
            )
            _log.warning("hooks_sse_stopped", error=str(exc), overloaded=overloaded)
            await sleep_until(stop, sse_reconnect_sleep(backoff))
            backoff = sse_backoff_ceiling(backoff, overloaded=overloaded)
