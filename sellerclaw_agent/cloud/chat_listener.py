"""Background task: SellerClaw chat SSE → local OpenClaw inbound."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import structlog

from sellerclaw_agent.async_backoff import sleep_until, sse_interval_after_error
from sellerclaw_agent.bundle.manifest import bundle_manifest_from_mapping
from sellerclaw_agent.cloud.auth_client import SellerClawAuthClient
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage, StoredCredentials
from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudConnectionError
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
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry
from sellerclaw_agent.server.storage import ManifestStorage

_log = structlog.get_logger(__name__)

_SSE_TIMEOUT = httpx.Timeout(connect=30.0, read=3600.0, write=30.0, pool=30.0)

# Refresh access token before SSE if JWT ``exp`` is missing or within this many seconds.
_ACCESS_REFRESH_SKEW_SEC = 120.0
# Throttle supervisord probes while draining SSE (each probe is a ``supervisorctl`` subprocess).
_OPENCLAW_PROBE_TTL_SEC = 5.0
# When the probe itself fails, keep the stale "error" verdict longer to avoid hot loops.
_OPENCLAW_PROBE_ERROR_TTL_SEC = 15.0


def _jwt_expires_at_epoch(access_token: str) -> float | None:
    """Return JWT ``exp`` as Unix time, or ``None`` if not a decodable JWT."""
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        pad = (-len(payload_b64)) % 4
        if pad:
            payload_b64 += "=" * pad
        raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        exp = data.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return None


_not_jwt_warning_emitted = False


async def _ensure_fresh_access_token(
    *,
    creds: StoredCredentials,
    creds_storage: CredentialsStorage,
    auth_client: SellerClawAuthClient,
    session_storage: EdgeSessionStorage,
    stop: asyncio.Event,
) -> StoredCredentials | None:
    """Proactively refresh the access token when the JWT ``exp`` is close.

    Returns updated credentials, the same credentials (no refresh needed or the
    token is not a JWT), or ``None`` to signal the caller to skip this loop
    iteration (auth failure or transient connection error).
    """
    global _not_jwt_warning_emitted
    exp = _jwt_expires_at_epoch(creds.access_token)
    if exp is None:
        if not _not_jwt_warning_emitted:
            _log.debug("access_token_not_jwt_skip_proactive_refresh")
            _not_jwt_warning_emitted = True
        return creds
    now = time.time()
    if exp > now + _ACCESS_REFRESH_SKEW_SEC:
        return creds
    try:
        new_access = await auth_client.refresh(refresh_token=creds.refresh_token)
    except CloudAuthError as exc:
        _log.warning("chat_sse_proactive_refresh_failed", error=str(exc))
        session_storage.clear()
        await sleep_until(stop, 10.0)
        return None
    except CloudConnectionError as exc:
        _log.warning("chat_sse_proactive_refresh_network_error", error=str(exc))
        await sleep_until(stop, 5.0)
        return None
    creds_storage.update_access_token(access_token=new_access)
    updated = creds_storage.load()
    return updated if updated is not None else creds


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
    return out


async def _consume_chat_sse(
    *,
    access_token: str,
    agent_instance_id: UUID,
    forwarder: LocalOpenClawForwarder,
    supervisor_mgr: SupervisorContainerManager,
    dedup: _MessageIdDedup,
    stop: asyncio.Event,
) -> None:
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

    base = get_sellerclaw_api_url().rstrip("/")
    url = f"{base}/agent/chat/stream"
    params = {"agent_instance_id": str(agent_instance_id)}
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=_SSE_TIMEOUT) as client:
        async with client.stream("GET", url, headers=headers, params=params) as response:
            if response.status_code == 401:
                raise CloudAuthError("chat_sse_unauthorized")
            if response.status_code == 403:
                raise CloudConnectionError("chat_sse_forbidden")
            response.raise_for_status()
            async for event_name, data in iter_sse_events(response):
                if stop.is_set():
                    break
                if event_name == "heartbeat":
                    continue
                if event_name == "error":
                    _log.warning("chat_sse_error_event", data_preview=data[:300])
                    continue
                if event_name != "user_message":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    _log.warning("chat_sse_invalid_json")
                    continue
                if not isinstance(payload, dict):
                    continue
                mid = str(payload.get("message_id") or "")
                if mid and dedup.already_forwarded(mid):
                    continue
                chat_id = str(payload.get("chat_id") or "") or None
                user_id = str(payload.get("user_id") or "") or None
                running, oc_status, oc_err = await _openclaw_gate()
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
                    continue
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
                except httpx.HTTPStatusError:
                    _log.exception(
                        "chat_forward_failed",
                        message_id=mid or "?",
                        chat_id=chat_id,
                        user_id=user_id,
                    )
                except Exception:
                    _log.exception(
                        "chat_forward_failed",
                        message_id=mid or "?",
                        chat_id=chat_id,
                        user_id=user_id,
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
    auth_client = SellerClawAuthClient()
    supervisor_mgr = create_supervisor_manager()
    dedup = _MessageIdDedup()
    backoff = 2.0

    while not stop.is_set():
        creds = creds_storage.load()
        if creds is None:
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
            manifest = bundle_manifest_from_mapping(mapping)
        except (TypeError, ValueError) as exc:
            _log.warning("chat_sse_manifest_invalid", error=str(exc))
            await sleep_until(stop, 10.0)
            continue

        creds = await _ensure_fresh_access_token(
            creds=creds,
            creds_storage=creds_storage,
            auth_client=auth_client,
            session_storage=session_storage,
            stop=stop,
        )
        if creds is None:
            continue

        try:
            async with httpx.AsyncClient(timeout=INBOUND_FORWARD_TIMEOUT) as oc_http:
                forwarder = LocalOpenClawForwarder(
                    base_url=openclaw_gateway_base_url(),
                    hooks_token=manifest.hooks_token,
                    http_client=oc_http,
                )

                if registry is not None:
                    registry.mark_sse_connected(True)
                try:
                    await _consume_chat_sse(
                        access_token=creds.access_token,
                        agent_instance_id=sess.agent_instance_id,
                        forwarder=forwarder,
                        supervisor_mgr=supervisor_mgr,
                        dedup=dedup,
                        stop=stop,
                    )
                finally:
                    if registry is not None:
                        registry.mark_sse_connected(False)
                backoff = 2.0
        except CloudAuthError:
            try:
                new_access = await auth_client.refresh(refresh_token=creds.refresh_token)
            except CloudAuthError as exc:
                _log.warning("chat_sse_token_refresh_failed", error=str(exc))
                session_storage.clear()
                await sleep_until(stop, 10.0)
                continue
            creds_storage.update_access_token(access_token=new_access)
            backoff = 2.0
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("chat_sse_stopped", error=str(exc))
            await sleep_until(stop, backoff)
            backoff = sse_interval_after_error(backoff)
