"""Background task: SellerClaw hooks SSE → local OpenClaw /hooks/agent."""

from __future__ import annotations

import asyncio
import json
import os
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
from sellerclaw_agent.http_clients import async_client
from sellerclaw_agent.server.secrets_store import get_secrets

_log = structlog.get_logger(__name__)

# Cloud sends a heartbeat every 15s on ``/agent/hooks/stream``. A tight read timeout
# (~4× heartbeat) makes silently-dead TCP connections observable; the outer loop
# will fall into its normal backoff+reconnect path.
_SSE_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)


async def forward_hook_event(
    payload: dict[str, Any],
    *,
    forwarder: LocalOpenClawForwarder,
    openclaw_gate: OpenClawGate,
) -> None:
    """Forward a ``hook_event`` to local OpenClaw ``/hooks/agent`` (gated on readiness)."""
    running, oc_status, oc_err = await openclaw_gate()
    if not running:
        _log.info(
            "hook_event_dropped",
            reason="openclaw_not_running",
            openclaw_status=oc_status,
            openclaw_error=oc_err,
        )
        return
    try:
        await forwarder.post_hooks_agent_json(payload)
    except httpx.ConnectError as exc:
        _log.info("hook_event_dropped", reason="gateway_unreachable", error=str(exc))
    except httpx.TimeoutException as exc:
        _log.info("hook_event_dropped", reason="gateway_timeout", error=str(exc))
    except Exception:
        _log.exception("hooks_forward_failed")


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
                await forward_hook_event(payload, forwarder=forwarder, openclaw_gate=openclaw_gate)


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
