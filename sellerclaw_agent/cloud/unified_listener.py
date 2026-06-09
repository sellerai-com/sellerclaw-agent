"""Background task: single SellerClaw edge SSE → local OpenClaw (chat + hooks merged).

Supersedes ``chat_listener`` + ``hooks_listener``: one ``/agent/stream`` connection carries
both chat (``user_message``/``cancel``) and hooks (``hook_event``) events, halving the number
of long-lived connections each agent holds to the cloud. Per-event handling is reused verbatim
from the two listeners; this module only adds the merged consume + its reconnect loop.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
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
from sellerclaw_agent.bundle.manifest import bundle_manifest_from_mapping
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token
from sellerclaw_agent.cloud.chat_listener import _MessageIdDedup, forward_cancel, forward_user_message
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.edge_sse_common import make_openclaw_gate, raise_for_edge_sse_status
from sellerclaw_agent.cloud.exceptions import (
    CloudAgentSuspendedError,
    CloudAuthError,
    CloudConnectionError,
    CloudConnectionInactiveError,
    CloudSessionInvalidatedError,
)
from sellerclaw_agent.cloud.hooks_listener import forward_hook_event
from sellerclaw_agent.cloud.openclaw_forwarder import (
    INBOUND_FORWARD_TIMEOUT,
    LocalOpenClawForwarder,
    openclaw_gateway_base_url,
)
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url
from sellerclaw_agent.cloud.sse_codec import iter_sse_events
from sellerclaw_agent.cloud.supervisor_manager import SupervisorContainerManager, create_supervisor_manager
from sellerclaw_agent.http_clients import async_client
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry
from sellerclaw_agent.server.secrets_store import get_secrets
from sellerclaw_agent.server.storage import ManifestStorage

_log = structlog.get_logger(__name__)

# Cloud heartbeats every 15s on ``/agent/stream``; a tight read timeout (~4× heartbeat) makes
# silently-dead TCP observable so the outer loop reconnects instead of sitting idle.
_SSE_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)


async def _consume_unified_sse(
    *,
    agent_token: str,
    agent_instance_id: UUID,
    forwarder: LocalOpenClawForwarder,
    supervisor_mgr: SupervisorContainerManager,
    dedup: _MessageIdDedup,
    stop: asyncio.Event,
) -> None:
    """Drain the unified SSE stream, routing each frame to the chat or hooks handler by name."""
    openclaw_gate = make_openclaw_gate(supervisor_mgr)
    base = get_sellerclaw_api_url().rstrip("/")
    url = f"{base}/agent/stream"
    params = {"agent_instance_id": str(agent_instance_id)}
    headers = {"Authorization": f"Bearer {agent_token}"}
    async with async_client(timeout=_SSE_TIMEOUT) as client:
        async with client.stream("GET", url, headers=headers, params=params) as response:
            await raise_for_edge_sse_status(response, label="edge_sse")
            async for event_name, data in iter_sse_events(response):
                if stop.is_set():
                    break
                if event_name == "heartbeat":
                    continue
                if event_name == "error":
                    _log.warning("edge_sse_error_event", data_preview=data[:300])
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    _log.warning("edge_sse_invalid_json")
                    continue
                if not isinstance(payload, dict):
                    continue
                if event_name == "user_message":
                    await forward_user_message(
                        payload, forwarder=forwarder, dedup=dedup, openclaw_gate=openclaw_gate
                    )
                elif event_name == "cancel":
                    await forward_cancel(payload, forwarder=forwarder)
                elif event_name == "hook_event":
                    await forward_hook_event(payload, forwarder=forwarder, openclaw_gate=openclaw_gate)


async def run_edge_unified_sse_loop(
    stop: asyncio.Event,
    *,
    registry: EdgeRuntimeRegistry | None = None,
) -> None:
    """Long-lived loop: connect to the unified cloud SSE and forward chat + hooks to OpenClaw."""
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
            _log.warning("edge_sse_manifest_invalid", error=str(exc))
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
                    registry.mark_hooks_sse_connected(True)
                try:
                    await _consume_unified_sse(
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
                        registry.mark_hooks_sse_connected(False)
                # Clean close usually means the cloud restarted/redeployed and dropped every
                # agent at once — jitter the reconnect so the fleet doesn't stampede.
                await sleep_until(stop, sse_clean_reconnect_sleep())
                backoff = 2.0
        except CloudSessionInvalidatedError as exc:
            _log.warning("edge_sse_session_invalidated_clearing_session", error=str(exc))
            session_storage.clear()
            await sleep_until(stop, 2.0)
            backoff = 2.0
            continue
        except CloudAuthError as exc:
            _log.warning("edge_sse_unauthorized_clearing_session", error=str(exc))
            session_storage.clear()
            await sleep_until(stop, 10.0)
            backoff = 2.0
            continue
        except CloudAgentSuspendedError as exc:
            _log.warning("edge_sse_agent_suspended_backing_off", error=str(exc))
            await sleep_until(stop, ping_interval_when_suspended())
            backoff = 2.0
            continue
        except CloudConnectionInactiveError as exc:
            _log.info("edge_sse_connection_inactive_retrying", error=str(exc))
            await sleep_until(stop, 5.0)
            backoff = 2.0
            continue
        except CloudConnectionError as exc:
            if str(exc) == "edge_sse_forbidden":
                _log.warning("edge_sse_forbidden_backing_off")
                await sleep_until(stop, ping_interval_when_suspended())
                backoff = 2.0
                continue
            _log.warning("edge_sse_stopped", error=str(exc))
            await sleep_until(stop, sse_reconnect_sleep(backoff))
            backoff = sse_backoff_ceiling(backoff)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            overloaded = isinstance(exc, httpx.HTTPStatusError) and is_overload_status(
                exc.response.status_code
            )
            _log.warning("edge_sse_stopped", error=str(exc), overloaded=overloaded)
            await sleep_until(stop, sse_reconnect_sleep(backoff))
            backoff = sse_backoff_ceiling(backoff, overloaded=overloaded)
            continue
