"""Background task: SellerClaw chat SSE → local OpenClaw inbound."""

from __future__ import annotations

import asyncio
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import structlog

from sellerclaw_agent.async_backoff import (
    is_overload_status,
    ping_interval_when_suspended,
    sleep_until,
    sse_backoff_ceiling,
    sse_clean_reconnect_sleep,
    sse_reconnect_sleep,
)
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token
from sellerclaw_agent.bundle.manifest import bundle_manifest_from_mapping
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
from sellerclaw_agent.cloud.openclaw_forwarder import (
    INBOUND_FORWARD_TIMEOUT,
    LocalOpenClawForwarder,
    openclaw_gateway_base_url,
)
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url
from sellerclaw_agent.cloud.sse_codec import iter_sse_events
from sellerclaw_agent.cloud.supervisor_manager import (
    SupervisorContainerManager,
    create_supervisor_manager,
)
from sellerclaw_agent.http_clients import async_client
from sellerclaw_agent.server.secrets_store import get_secrets
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry
from sellerclaw_agent.server.storage import ManifestStorage

_log = structlog.get_logger(__name__)

# Cloud heartbeats every 15s (see ``iter_agent_edge_chat_sse`` on the server). A tight
# read timeout (~4× heartbeat) lets us detect silently-dead TCP (proxy NAT drop,
# container pause) promptly and reconnect, instead of sitting idle for up to an hour.
_SSE_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)


class _MessageIdDedup:
    """LRU set of successfully forwarded ``message_id`` values (empty id is never deduped)."""

    def __init__(self, max_size: int = 4096) -> None:
        self._max = max_size
        self._order: OrderedDict[str, None] = OrderedDict()

    def already_forwarded(self, message_id: str) -> bool:
        if not message_id.strip():
            return False
        if message_id in self._order:
            self._order.move_to_end(message_id)
            return True
        return False

    def record_forwarded(self, message_id: str) -> None:
        if not message_id.strip():
            return
        self._order[message_id] = None
        while len(self._order) > self._max:
            self._order.popitem(last=False)


def _inbound_body_from_sse(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip server-only fields; keep sellerclaw-ui inbound contract."""
    out: dict[str, Any] = {
        "chat_id": str(payload["chat_id"]),
        "agent_id": str(payload["agent_id"]),
        "user_id": str(payload["user_id"]),
        "text": str(payload["text"]),
    }
    mid = payload.get("message_id")
    if mid is not None and str(mid).strip():
        out["message_id"] = str(mid)
    raw = payload.get("raw_content")
    if raw is not None:
        out["raw_content"] = raw
    # Catch-up re-delivery of a still-PROCESSING message: tells the local handler to
    # dispatch it as a fresh OpenClaw turn (new MessageSid) and to guard against a
    # genuinely in-flight duplicate, instead of treating it as a brand-new message.
    if payload.get("redelivery"):
        out["redelivery"] = True
    return out


async def forward_cancel(payload: dict[str, Any], *, forwarder: LocalOpenClawForwarder) -> None:
    """Forward a ``cancel`` event: abort the in-flight OpenClaw run for this chat (best-effort)."""
    cancel_chat_id = str(payload.get("chat_id") or "") or None
    cancel_agent_id = str(payload.get("agent_id") or "") or None
    if not cancel_chat_id or not cancel_agent_id:
        _log.warning("chat_cancel_missing_fields", chat_id=cancel_chat_id, agent_id=cancel_agent_id)
        return
    try:
        await forwarder.post_abort_json({"chat_id": cancel_chat_id, "agent_id": cancel_agent_id})
    except httpx.ConnectError as exc:
        _log.info("chat_cancel_dropped", reason="gateway_unreachable", chat_id=cancel_chat_id, error=str(exc))
    except httpx.TimeoutException as exc:
        _log.info("chat_cancel_dropped", reason="gateway_timeout", chat_id=cancel_chat_id, error=str(exc))
    except Exception:
        _log.exception("chat_cancel_forward_failed", chat_id=cancel_chat_id)


async def forward_user_message(
    payload: dict[str, Any],
    *,
    forwarder: LocalOpenClawForwarder,
    dedup: _MessageIdDedup,
    openclaw_gate: OpenClawGate,
) -> None:
    """Forward a ``user_message`` event to local OpenClaw inbound (deduped, gated on readiness)."""
    mid = str(payload.get("message_id") or "")
    # Catch-up re-delivery bypasses the dedup: the cloud only re-sends a message that is
    # still PROCESSING (its turn never completed on the cloud), so it MUST be re-processed.
    # The dedup — recorded the instant a message is handed to the local inbound (HTTP 202),
    # not when its turn is actually delivered back — would otherwise drop the re-delivery as
    # "already forwarded" and leave the message stuck PROCESSING forever. Double-processing
    # of a genuinely in-flight turn is prevented downstream by the inbound handler's
    # per-message in-flight guard.
    redelivery = bool(payload.get("redelivery"))
    if mid and not redelivery and dedup.already_forwarded(mid):
        return
    chat_id = str(payload.get("chat_id") or "") or None
    user_id = str(payload.get("user_id") or "") or None
    running, oc_status, oc_err = await openclaw_gate()
    if not running:
        _log.info(
            "chat_message_dropped",
            reason="openclaw_not_running",
            openclaw_status=oc_status,
            openclaw_error=oc_err,
            message_id=mid or None,
            chat_id=chat_id,
            user_id=user_id,
        )
        return
    try:
        body = _inbound_body_from_sse(payload)
        await forwarder.post_inbound_json(body)
        if mid:
            dedup.record_forwarded(mid)
    except httpx.ConnectError as exc:
        _log.info(
            "chat_message_dropped",
            reason="gateway_unreachable",
            message_id=mid or None,
            chat_id=chat_id,
            user_id=user_id,
            error=str(exc),
        )
    except httpx.TimeoutException as exc:
        _log.info(
            "chat_message_dropped",
            reason="gateway_timeout",
            message_id=mid or None,
            chat_id=chat_id,
            user_id=user_id,
            error=str(exc),
        )
    except Exception:
        _log.exception("chat_forward_failed", message_id=mid or "?", chat_id=chat_id, user_id=user_id)


async def _consume_chat_sse(
    *,
    agent_token: str,
    agent_instance_id: UUID,
    forwarder: LocalOpenClawForwarder,
    supervisor_mgr: SupervisorContainerManager,
    dedup: _MessageIdDedup,
    stop: asyncio.Event,
) -> None:
    _openclaw_gate = make_openclaw_gate(supervisor_mgr)

    base = get_sellerclaw_api_url().rstrip("/")
    url = f"{base}/agent/chat/stream"
    params = {"agent_instance_id": str(agent_instance_id)}
    headers = {"Authorization": f"Bearer {agent_token}"}
    async with async_client(timeout=_SSE_TIMEOUT) as client:
        async with client.stream("GET", url, headers=headers, params=params) as response:
            await raise_for_edge_sse_status(response, label="chat_sse")
            async for event_name, data in iter_sse_events(response):
                if stop.is_set():
                    break
                if event_name == "heartbeat":
                    continue
                if event_name == "error":
                    _log.warning("chat_sse_error_event", data_preview=data[:300])
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    _log.warning("chat_sse_invalid_json")
                    continue
                if not isinstance(payload, dict):
                    continue
                if event_name == "cancel":
                    await forward_cancel(payload, forwarder=forwarder)
                    continue
                if event_name == "user_message":
                    await forward_user_message(
                        payload, forwarder=forwarder, dedup=dedup, openclaw_gate=_openclaw_gate
                    )


async def run_edge_chat_sse_loop(
    stop: asyncio.Event,
    *,
    registry: EdgeRuntimeRegistry | None = None,
) -> None:
    """Long-lived loop: connect to cloud chat SSE and forward ``user_message`` to OpenClaw."""
    data_dir = Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    creds_storage = CredentialsStorage(data_dir)
    session_storage = EdgeSessionStorage(data_dir)
    supervisor_mgr = create_supervisor_manager()
    dedup = _MessageIdDedup()
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

        storage = ManifestStorage(data_dir)
        mapping = storage.load()
        if mapping is None:
            await sleep_until(stop, 10.0)
            continue
        try:
            bundle_manifest_from_mapping(mapping)
        except (TypeError, ValueError) as exc:
            _log.warning("chat_sse_manifest_invalid", error=str(exc))
            await sleep_until(stop, 10.0)
            continue

        try:
            async with async_client(timeout=INBOUND_FORWARD_TIMEOUT) as oc_http:
                forwarder = LocalOpenClawForwarder(
                    base_url=openclaw_gateway_base_url(),
                    hooks_token=get_secrets(data_dir).hooks_token,
                    http_client=oc_http,
                )

                if registry is not None:
                    registry.mark_sse_connected(True)
                try:
                    await _consume_chat_sse(
                        agent_token=bearer,
                        agent_instance_id=sess.agent_instance_id,
                        forwarder=forwarder,
                        supervisor_mgr=supervisor_mgr,
                        dedup=dedup,
                        stop=stop,
                    )
                finally:
                    if registry is not None:
                        registry.mark_sse_connected(False)
                # Clean close usually means the cloud restarted/redeployed and dropped every
                # agent at once — jitter the reconnect so the fleet doesn't stampede.
                await sleep_until(stop, sse_clean_reconnect_sleep())
                backoff = 2.0
        except CloudSessionInvalidatedError as exc:
            _log.warning("chat_sse_session_invalidated_clearing_session", error=str(exc))
            session_storage.clear()
            await sleep_until(stop, 2.0)
            backoff = 2.0
            continue
        except CloudAuthError as exc:
            _log.warning("chat_sse_unauthorized_clearing_session", error=str(exc))
            session_storage.clear()
            await sleep_until(stop, 10.0)
            backoff = 2.0
            continue
        except CloudAgentSuspendedError as exc:
            _log.warning("chat_sse_agent_suspended_backing_off", error=str(exc))
            await sleep_until(stop, ping_interval_when_suspended())
            backoff = 2.0
            continue
        except CloudConnectionInactiveError as exc:
            _log.info("chat_sse_connection_inactive_retrying", error=str(exc))
            await sleep_until(stop, 5.0)
            backoff = 2.0
            continue
        except CloudConnectionError as exc:
            if str(exc) == "chat_sse_forbidden":
                _log.warning("chat_sse_forbidden_backing_off")
                await sleep_until(stop, ping_interval_when_suspended())
                backoff = 2.0
                continue
            _log.warning("chat_sse_stopped", error=str(exc))
            await sleep_until(stop, sse_reconnect_sleep(backoff))
            backoff = sse_backoff_ceiling(backoff)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            overloaded = isinstance(exc, httpx.HTTPStatusError) and is_overload_status(
                exc.response.status_code
            )
            _log.warning("chat_sse_stopped", error=str(exc), overloaded=overloaded)
            await sleep_until(stop, sse_reconnect_sleep(backoff))
            backoff = sse_backoff_ceiling(backoff, overloaded=overloaded)
