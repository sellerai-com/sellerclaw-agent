from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from sellerclaw_agent.cloud.auth_client import SellerClawAuthClient
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import CloudAgentSuspendedError, CloudAuthError, CloudConnectionError
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url

_DEFAULT_TIMEOUT = httpx.Timeout(30.0)
_STATE_BACKUP_TIMEOUT = httpx.Timeout(600.0, connect=30.0)


@dataclass(frozen=True)
class ConnectResponse:
    agent_instance_id: UUID


@dataclass(frozen=True)
class PendingCommandPayload:
    command_id: UUID
    command_type: str
    issued_at: datetime


@dataclass(frozen=True)
class PingResponse:
    pending_command: PendingCommandPayload | None


class SellerClawConnectionClient:
    """HTTP client for ``/agent/connection/*`` (JWT or agent token)."""

    def __init__(
        self,
        *,
        credentials_storage: CredentialsStorage,
        auth_client: SellerClawAuthClient | None = None,
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._creds = credentials_storage
        self._auth = auth_client or SellerClawAuthClient()
        self._base = (base_url if base_url is not None else get_sellerclaw_api_url()).rstrip("/")
        self._timeout = timeout or _DEFAULT_TIMEOUT
        self._transport = transport

    def _detail_message(self, payload: Any) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict):
                msg = detail.get("message")
                if isinstance(msg, str):
                    return msg
            if isinstance(detail, str):
                return detail
        return "Request failed"

    @staticmethod
    def _is_agent_suspended_payload(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return detail.get("code") == "agent_suspended"
        return False

    def _resolve_bearer_token(self) -> tuple[str, bool]:
        """Return (token, supports_jwt_refresh). Agent API key has no refresh."""
        stored = self._creds.load()
        if stored is not None:
            return stored.access_token, True
        agent_key = (os.environ.get("AGENT_API_KEY") or "").strip()
        if agent_key:
            return agent_key, False
        raise CloudConnectionError("Not authenticated: missing credentials and AGENT_API_KEY")

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        access, supports_refresh = self._resolve_bearer_token()

        async def _call(bearer: str) -> httpx.Response:
            url = f"{self._base}{path}"
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                return await client.request(
                    method,
                    url,
                    headers={
                        "Authorization": f"Bearer {bearer}",
                        "Content-Type": "application/json",
                    },
                    json=json_body,
                )

        response = await _call(access)
        if response.status_code == 401:
            if not supports_refresh:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                raise CloudAuthError(self._detail_message(data), status_code=401)
            stored = self._creds.load()
            if stored is None:
                raise CloudConnectionError("Credentials missing for refresh")
            try:
                new_access = await self._auth.refresh(refresh_token=stored.refresh_token)
            except CloudAuthError as exc:
                raise CloudConnectionError(str(exc)) from exc
            self._creds.update_access_token(access_token=new_access)
            reloaded = self._creds.load()
            if reloaded is None:
                raise CloudConnectionError("Credentials missing after refresh")
            response = await _call(reloaded.access_token)

        if response.status_code >= 500:
            raise CloudConnectionError(f"SellerClaw server error: HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise CloudConnectionError("Invalid JSON response") from exc
        if response.status_code == 403 and self._is_agent_suspended_payload(data):
            raise CloudAgentSuspendedError(self._detail_message(data))
        return response.status_code, data

    async def _request_raw(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        access, supports_refresh = self._resolve_bearer_token()

        effective_timeout = timeout or self._timeout

        async def _call(bearer: str) -> httpx.Response:
            url = f"{self._base}{path}"
            headers: dict[str, str] = {"Authorization": f"Bearer {bearer}"}
            if body is not None:
                headers["Content-Type"] = content_type or "application/octet-stream"
            async with httpx.AsyncClient(timeout=effective_timeout, transport=self._transport) as client:
                return await client.request(method, url, headers=headers, content=body)

        response = await _call(access)
        if response.status_code == 401:
            if not supports_refresh:
                try:
                    payload = response.json() if response.content else {}
                except ValueError:
                    payload = {}
                raise CloudAuthError(self._detail_message(payload), status_code=401)
            stored = self._creds.load()
            if stored is None:
                raise CloudConnectionError("Credentials missing for refresh")
            try:
                new_access = await self._auth.refresh(refresh_token=stored.refresh_token)
            except CloudAuthError as exc:
                raise CloudConnectionError(str(exc)) from exc
            self._creds.update_access_token(access_token=new_access)
            reloaded = self._creds.load()
            if reloaded is None:
                raise CloudConnectionError("Credentials missing after refresh")
            response = await _call(reloaded.access_token)
        return response

    async def upload_state_backup(self, archive: bytes) -> bool:
        """Upload gzip tar state backup; return True if server accepted (204)."""
        response = await self._request_raw(
            "POST",
            "/agent/connection/state-backup",
            body=archive,
            content_type="application/gzip",
            timeout=_STATE_BACKUP_TIMEOUT,
        )
        if response.status_code == 401:
            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = {}
            raise CloudAuthError(self._detail_message(payload), status_code=401)
        if response.status_code >= 500:
            raise CloudConnectionError(f"SellerClaw server error: HTTP {response.status_code}")
        if response.status_code == 204:
            return True
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            raise CloudConnectionError(self._detail_message(payload))
        return False

    async def download_state_backup(self) -> bytes | None:
        """Download latest backup or None if 404."""
        response = await self._request_raw(
            "GET",
            "/agent/connection/state-backup",
            timeout=_STATE_BACKUP_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        if response.status_code == 401:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            raise CloudAuthError(self._detail_message(payload), status_code=401)
        if response.status_code >= 500:
            raise CloudConnectionError(f"SellerClaw server error: HTTP {response.status_code}")
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            raise CloudConnectionError(self._detail_message(payload))
        return response.content

    async def connect(self, *, agent_version: str, protocol_version: int) -> ConnectResponse:
        status, data = await self._request_json(
            "POST",
            "/agent/connection/connect",
            json_body={"agent_version": agent_version, "protocol_version": protocol_version},
        )
        if status == 401:
            raise CloudAuthError(self._detail_message(data), status_code=401)
        if status >= 400:
            raise CloudConnectionError(self._detail_message(data))
        if not isinstance(data, dict):
            raise CloudConnectionError("Invalid connect response")
        raw_id = data.get("agent_instance_id")
        if not raw_id:
            raise CloudConnectionError("connect response missing agent_instance_id")
        return ConnectResponse(agent_instance_id=UUID(str(raw_id)))

    async def fetch_edge_manifest(self) -> dict[str, Any]:
        status, data = await self._request_json("GET", "/agent/connection/edge-manifest")
        if status == 401:
            raise CloudAuthError(self._detail_message(data), status_code=401)
        if status >= 400:
            raise CloudConnectionError(self._detail_message(data))
        if not isinstance(data, dict):
            raise CloudConnectionError("Invalid edge-manifest response")
        return data

    async def ping(
        self,
        *,
        agent_instance_id: UUID,
        agent_version: str,
        protocol_version: int,
        openclaw_status: str,
        openclaw_error: str | None,
        command_result: dict[str, Any] | None,
    ) -> PingResponse:
        body: dict[str, Any] = {
            "agent_instance_id": str(agent_instance_id),
            "agent_version": agent_version,
            "protocol_version": protocol_version,
            "openclaw_status": openclaw_status,
            "openclaw_error": openclaw_error,
            "command_result": command_result,
        }
        status, data = await self._request_json("POST", "/agent/connection/ping", json_body=body)
        if status == 401:
            raise CloudAuthError(self._detail_message(data), status_code=401)
        if status >= 400:
            raise CloudConnectionError(self._detail_message(data))
        if not isinstance(data, dict):
            raise CloudConnectionError("Invalid ping response")
        pending_raw = data.get("pending_command")
        if pending_raw is None:
            return PingResponse(pending_command=None)
        if not isinstance(pending_raw, dict):
            raise CloudConnectionError("Invalid pending_command")
        issued_raw = pending_raw.get("issued_at")
        if issued_raw is None:
            raise CloudConnectionError("pending_command missing issued_at")
        issued = datetime.fromisoformat(str(issued_raw).replace("Z", "+00:00"))
        return PingResponse(
            pending_command=PendingCommandPayload(
                command_id=UUID(str(pending_raw["command_id"])),
                command_type=str(pending_raw["command_type"]),
                issued_at=issued,
            ),
        )

    async def disconnect(self, *, agent_instance_id: UUID) -> None:
        status, data = await self._request_json(
            "POST",
            "/agent/connection/disconnect",
            json_body={"agent_instance_id": str(agent_instance_id)},
        )
        if status == 401:
            raise CloudAuthError(self._detail_message(data), status_code=401)
        if status >= 400:
            raise CloudConnectionError(self._detail_message(data))
