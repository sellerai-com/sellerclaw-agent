"""Background task: SellerClaw hooks SSE → local OpenClaw /hooks/agent."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import structlog

from sellerclaw_agent.async_backoff import ping_interval_when_suspended, sleep_until, sse_interval_after_error
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import (
    CloudAgentSuspendedError,
    CloudAuthError,
    CloudConnectionError,
    CloudConnectionInactiveError,
    CloudSessionInvalidatedError,
    agent_api_error_code,
)
from sellerclaw_agent.cloud.openclaw_forwarder import (
    INBOUND_FORWARD_TIMEOUT,
    LocalOpenClawForwarder,
    openclaw_gateway_base_url,
)
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url
from sellerclaw_agent.cloud.sse_codec import iter_sse_events
from sellerclaw_agent.server.secrets_store import get_secrets

_log = structlog.get_logger(__name__)

# Cloud sends a heartbeat every 15s on ``/agent/hooks/stream``. A tight read timeout
# (~4× heartbeat) makes silently-dead TCP connections observable; the outer loop
# will fall into its normal backoff+reconnect path.
_SSE_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)
_OPENCLAW_PROBE_TTL_SEC = 5.0
_OPENCLAW_PROBE_ERROR_TTL_SEC = 15.0


def _api_detail_message(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        msg = detail.get("message")
        if isinstance(msg, str):
            return msg
    if isinstance(detail, str):
        return detail
    return "Request failed"


async def _error_response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        await response.aread()
    except Exception:
        return {}
    try:
        raw = response.json()
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


async def _consume_hooks_sse(
    *,
    agent_token: str,
    agent_instance_id: UUID,
    forwarder: LocalOpenClawForwarder,
    probe_openclaw_running: Any,
    stop: asyncio.Event,
) -> None:
    base = get_sellerclaw_api_url().rstrip("/")
    url = f"{base}/agent/hooks/stream"
    params = {"agent_instance_id": str(agent_instance_id)}
    headers = {"Authorization": f"Bearer {agent_token}"}
    async with httpx.AsyncClient(timeout=_SSE_TIMEOUT) as client:
        async with client.stream("GET", url, headers=headers, params=params) as response:
            if response.status_code == 401:
                raise CloudAuthError("hooks_sse_unauthorized", status_code=401)
            if response.status_code == 403:
                err_body = await _error_response_json(response)
                code = agent_api_error_code(err_body)
                if code == "agent_suspended":
                    raise CloudAgentSuspendedError(_api_detail_message(err_body))
                if code == "agent_session_invalidated":
                    raise CloudSessionInvalidatedError(
                        _api_detail_message(err_body) or "hooks_sse_session_invalidated",
                        status_code=403,
                    )
                if code in ("agent_connection_inactive", "agent_connection_not_found"):
                    raise CloudConnectionInactiveError(
                        _api_detail_message(err_body) or "hooks_sse_connection_inactive"
                    )
                raise CloudConnectionError("hooks_sse_forbidden")
            response.raise_for_status()
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
                running, oc_status, oc_err = await probe_openclaw_running()
                if not running:
                    _log.info(
                        "hook_event_dropped",
                        reason="openclaw_not_running",
                        openclaw_status=oc_status,
                        openclaw_error=oc_err,
                    )
                    continue
                try:
                    await forwarder.post_hooks_agent_json(payload)
                except httpx.ConnectError as exc:
                    _log.info("hook_event_dropped", reason="gateway_unreachable", error=str(exc))
                except httpx.TimeoutException as exc:
                    _log.info("hook_event_dropped", reason="gateway_timeout", error=str(exc))
                except httpx.HTTPStatusError:
                    _log.exception("hooks_forward_failed")
                except Exception:
                    _log.exception("hooks_forward_failed")


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
    probe_at = 0.0
    probe_status = ""
    probe_err: str | None = None

    async def _openclaw_gate() -> tuple[bool, str, str | None]:
        nonlocal probe_at, probe_status, probe_err
        now_m = time.monotonic()
        ttl = _OPENCLAW_PROBE_ERROR_TTL_SEC if probe_status == "error" else _OPENCLAW_PROBE_TTL_SEC
        if probe_status and now_m - probe_at < ttl:
            return probe_status == "running", probe_status, probe_err
        probe_at = now_m
        probe_status, probe_err = await asyncio.to_thread(supervisor_mgr.probe_openclaw_status)
        return probe_status == "running", probe_status, probe_err

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
            async with httpx.AsyncClient(timeout=INBOUND_FORWARD_TIMEOUT) as oc_http:
                forwarder = LocalOpenClawForwarder(
                    base_url=openclaw_gateway_base_url(),
                    hooks_token=get_secrets(data_dir).hooks_token,
                    http_client=oc_http,
                )
                if registry is not None:
                    registry.mark_hooks_sse_connected(True)
                try:
                    await _consume_hooks_sse(
                        agent_token=bearer,
                        agent_instance_id=sess.agent_instance_id,
                        forwarder=forwarder,
                        probe_openclaw_running=_openclaw_gate,
                        stop=stop,
                    )
                finally:
                    if registry is not None:
                        registry.mark_hooks_sse_connected(False)
                backoff = 2.0
        except CloudSessionInvalidatedError as exc:
            _log.warning("hooks_sse_session_invalidated_clearing_session", error=str(exc))
            session_storage.clear()
            await sleep_until(stop, 2.0)
            backoff = 2.0
            continue
        except CloudAuthError:
            _log.warning("hooks_sse_unauthorized_clearing_local_auth")
            creds_storage.clear()
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
            await sleep_until(stop, backoff)
            backoff = sse_interval_after_error(backoff)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("hooks_sse_stopped", error=str(exc))
            await sleep_until(stop, backoff)
            backoff = sse_interval_after_error(backoff)
