from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx

from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import (
    CONNECT_REFUSAL_ERRORS,
    CloudAgentSuspendedError,
    CloudAuthError,
    CloudConnectionError,
    CloudMoveRefusedError,
    CloudSessionInvalidatedError,
    agent_api_error_code,
    is_agent_suspended_api_payload,
)
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url
from sellerclaw_agent.http_clients import async_client

# A connection that is not up in 10 s will not come up: on a network that drops SYNs (VPNs, flaky
# Wi-Fi) a fresh attempt gets through sooner than waiting out the full 30 s on the stuck one.
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_STATE_BACKUP_TIMEOUT = httpx.Timeout(600.0, connect=30.0)

RunsOn = Literal["cloud", "computer"]
MoveStatus = Literal["moving", "moved", "already_here"]
_MOVE_STATUSES: frozenset[str] = frozenset({"moving", "moved", "already_here"})


def detect_runs_on(env: Mapping[str, str] | None = None) -> RunsOn:
    """Where this agent runs, as told to the cloud on ``connect`` (protocol 10).

    ``cloud`` on a Fly machine (Fly sets ``FLY_MACHINE_ID`` itself) or when the image is told so
    (``SELLERCLAW_RUNS_ON=cloud``); anywhere else it is the owner's own computer. Only a hint: the
    cloud trusts the token it issued to its own machine more than this field.
    """
    source = os.environ if env is None else env
    if (source.get("FLY_MACHINE_ID") or "").strip():
        return "cloud"
    if (source.get("SELLERCLAW_RUNS_ON") or "").strip().lower() == "cloud":
        return "cloud"
    return "computer"


def openclaw_status_for_connect(probe_status: str | None) -> Literal["running", "stopped"] | None:
    """The supervisor probe (running / starting / stopped / error) as ``connect`` reports it.

    A gateway still coming up counts as running — the cloud must not send START into it. An error
    (FATAL, BACKOFF, supervisorctl unreachable) is unknown and left out.
    """
    if probe_status in ("running", "starting"):
        return "running"
    if probe_status == "stopped":
        return "stopped"
    return None


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


@dataclass(frozen=True)
class WhatsappLinkRequest:
    """The cloud's open WhatsApp link attempt the ``whatsapp_link`` command refers to."""

    login_id: UUID
    force: bool


class SellerClawConnectionClient:
    """HTTP client for ``/agent/connection/*`` (``sca_`` agent token only)."""

    def __init__(
        self,
        *,
        credentials_storage: CredentialsStorage,
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._creds = credentials_storage
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

    def _resolve_bearer_token(self) -> str:
        token = resolve_agent_bearer_token(self._creds)
        if not token:
            raise CloudConnectionError("Not authenticated: missing agent_token.json and AGENT_API_KEY")
        return token

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> tuple[int, Any]:
        bearer = self._resolve_bearer_token()

        async def _call(token: str) -> httpx.Response:
            url = f"{self._base}{path}"
            async with async_client(timeout=timeout or self._timeout, transport=self._transport) as client:
                return await client.request(
                    method,
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=json_body,
                )

        try:
            response = await _call(bearer)
        except httpx.RequestError as exc:
            raise CloudConnectionError(str(exc)) from exc
        if response.status_code == 401:
            try:
                data = response.json()
            except ValueError:
                data = {}
            if agent_api_error_code(data) == "agent_session_invalidated":
                raise CloudSessionInvalidatedError(self._detail_message(data), status_code=401)
            raise CloudAuthError(self._detail_message(data), status_code=401)

        if response.status_code >= 500:
            raise CloudConnectionError(f"SellerClaw server error: HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise CloudConnectionError("Invalid JSON response") from exc
        if response.status_code == 403 and is_agent_suspended_api_payload(data):
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
        bearer = self._resolve_bearer_token()
        effective_timeout = timeout or self._timeout

        async def _call(token: str) -> httpx.Response:
            url = f"{self._base}{path}"
            headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
            if body is not None:
                headers["Content-Type"] = content_type or "application/octet-stream"
            async with async_client(timeout=effective_timeout, transport=self._transport) as client:
                return await client.request(method, url, headers=headers, content=body)

        try:
            response = await _call(bearer)
        except httpx.RequestError as exc:
            raise CloudConnectionError(str(exc)) from exc
        if response.status_code == 401:
            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = {}
            raise CloudAuthError(self._detail_message(payload), status_code=401)
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

    async def _post_cron_json(self, path: str, body: dict[str, Any]) -> bool:
        """POST a report (cron, session question); True when accepted, False when this cloud has no
        such endpoint.

        An older cloud (before the agent protocol that brought the report) answers 404 / 405 / 501:
        the caller drops the report quietly instead of treating it as a failure.
        """
        response = await self._request_raw(
            "POST",
            path,
            body=json.dumps(body).encode("utf-8"),
            content_type="application/json",
        )
        if response.status_code in (404, 405, 501):
            return False
        if response.status_code >= 500:
            raise CloudConnectionError(f"SellerClaw server error: HTTP {response.status_code}")
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            raise CloudConnectionError(self._detail_message(payload))
        return True

    async def post_cron_command_result(self, *, request_id: str, result: dict[str, Any]) -> bool:
        """Report the outcome of one ``cron_command`` (``POST /agent/cron/commands/{request_id}``)."""
        return await self._post_cron_json(f"/agent/cron/commands/{request_id}", result)

    async def post_session_question_answer(self, *, request_id: str, result: dict[str, Any]) -> bool:
        """Report the answer to one ``session_question`` (``POST /agent/session-questions/{request_id}``)."""
        return await self._post_cron_json(f"/agent/session-questions/{request_id}", result)

    async def post_cron_runs(self, runs: list[dict[str, Any]]) -> bool:
        """Report finished cron runs (``POST /agent/cron/runs``); the cloud merges repeats."""
        return await self._post_cron_json("/agent/cron/runs", {"runs": runs})

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

    def _raise_for_raw_error(self, response: httpx.Response) -> None:
        """Map a non-2xx raw response to the client's exceptions (401 → auth, else connection)."""
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        if response.status_code == 401:
            raise CloudAuthError(self._detail_message(payload), status_code=401)
        if response.status_code >= 500:
            raise CloudConnectionError(f"SellerClaw server error: HTTP {response.status_code}")
        raise CloudConnectionError(self._detail_message(payload))

    async def fetch_whatsapp_link(self) -> WhatsappLinkRequest | None:
        """The open WhatsApp link attempt, or None when the cloud has none (stop signal)."""
        status, data = await self._request_json("GET", "/agent/connection/whatsapp/link")
        if status == 404:
            return None
        if status >= 400:
            raise CloudConnectionError(self._detail_message(data))
        if not isinstance(data, dict) or not data.get("login_id"):
            raise CloudConnectionError("Invalid WhatsApp link response")
        return WhatsappLinkRequest(
            login_id=UUID(str(data["login_id"])),
            force=bool(data.get("force")),
        )

    async def post_whatsapp_link_state(self, *, login_id: UUID, state: dict[str, Any]) -> bool:
        """Report one step of a link attempt; False once the cloud no longer knows ``login_id``.

        A 404 means the owner closed or replaced the attempt — the runner stops on it.
        """
        response = await self._request_raw(
            "POST",
            f"/agent/connection/whatsapp/link/{login_id}",
            body=json.dumps(state).encode("utf-8"),
            content_type="application/json",
        )
        if response.status_code == 404:
            return False
        if response.status_code >= 300:
            self._raise_for_raw_error(response)
        return True

    async def upload_whatsapp_session(self, archive: bytes) -> None:
        """Store the WhatsApp session archive in the cloud (replaces the previous one)."""
        response = await self._request_raw(
            "POST",
            "/agent/connection/whatsapp/session",
            body=archive,
            content_type="application/gzip",
            timeout=_STATE_BACKUP_TIMEOUT,
        )
        if response.status_code >= 300:
            self._raise_for_raw_error(response)

    async def download_whatsapp_session(self) -> bytes | None:
        """The stored WhatsApp session archive, or None when there is none."""
        response = await self._request_raw(
            "GET",
            "/agent/connection/whatsapp/session",
            timeout=_STATE_BACKUP_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 300:
            self._raise_for_raw_error(response)
        return response.content

    async def delete_whatsapp_session(self) -> None:
        """Drop the stored WhatsApp session (the owner logged the assistant out)."""
        response = await self._request_raw("DELETE", "/agent/connection/whatsapp/session")
        if response.status_code >= 300 and response.status_code != 404:
            self._raise_for_raw_error(response)

    async def connect(
        self,
        *,
        agent_version: str,
        protocol_version: int,
        runs_on: RunsOn | None = None,
        openclaw_status: Literal["running", "stopped"] | None = None,
    ) -> ConnectResponse:
        body: dict[str, Any] = {"agent_version": agent_version, "protocol_version": protocol_version}
        if runs_on is not None:
            # Protocol 10: the owner's computer or a SellerClaw cloud machine.
            body["runs_on"] = runs_on
        if openclaw_status is not None:
            # Protocol 10: lets the cloud start a stopped agent on its own and not send START to one
            # that is already up (a reconnect after a network blip).
            body["openclaw_status"] = openclaw_status
        status, data = await self._request_json("POST", "/agent/connection/connect", json_body=body)
        if status == 401:
            raise CloudAuthError(self._detail_message(data), status_code=401)
        refusal = CONNECT_REFUSAL_ERRORS.get(agent_api_error_code(data) or "") if status >= 400 else None
        if refusal is not None:
            raise refusal(self._detail_message(data))
        if status >= 400:
            raise CloudConnectionError(self._detail_message(data))
        if not isinstance(data, dict):
            raise CloudConnectionError("Invalid connect response")
        raw_id = data.get("agent_instance_id")
        if not raw_id:
            raise CloudConnectionError("connect response missing agent_instance_id")
        return ConnectResponse(agent_instance_id=UUID(str(raw_id)))

    def bearer_token(self) -> str:
        """Current ``sca_`` bearer token (for non-HTTP transports, e.g. websockets)."""
        return self._resolve_bearer_token()

    async def move_here(self) -> MoveStatus:
        """Ask the cloud to move the owner's agent to this computer (``POST /agent/hosting/move-here``).

        ``moving`` — the cloud machine hands over once this one connects; ``moved`` — there was no
        cloud machine, this computer is the agent's place already; ``already_here``. A refusal
        (e.g. a cloud machine still starting) raises :class:`CloudMoveRefusedError`.
        """
        status, data = await self._request_json("POST", "/agent/hosting/move-here")
        if status >= 400:
            raise CloudMoveRefusedError(
                self._detail_message(data),
                code=agent_api_error_code(data),
                status_code=status,
            )
        result = data.get("status") if isinstance(data, dict) else None
        if result not in _MOVE_STATUSES:
            raise CloudConnectionError("Invalid move-here response")
        return result  # type: ignore[return-value]

    async def fetch_browser_view_session_id(self) -> UUID | None:
        """Active browser-view session for this agent, or None when there is none.

        ``None`` is the stop signal for the tunnel runner: the session expired or
        the user closed it, so the agent must not redial.
        """
        status, data = await self._request_json("GET", "/agent/browser-view/session")
        if status == 404:
            return None
        if status == 401:
            raise CloudAuthError(self._detail_message(data), status_code=401)
        if status >= 400:
            raise CloudConnectionError(self._detail_message(data))
        if not isinstance(data, dict) or not data.get("session_id"):
            raise CloudConnectionError("Invalid browser-view session response")
        return UUID(str(data["session_id"]))

    async def fetch_edge_manifest(self, *, timeout: httpx.Timeout | None = None) -> dict[str, Any]:
        status, data = await self._request_json("GET", "/agent/connection/edge-manifest", timeout=timeout)
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
        browser: dict[str, Any] | None = None,
        agent_activity: dict[str, Any] | None = None,
        config_version: int | None = None,
        openclaw_version: str | None = None,
        whatsapp: dict[str, Any] | None = None,
        ai_subscription: dict[str, Any] | None = None,
    ) -> PingResponse:
        body: dict[str, Any] = {
            "agent_instance_id": str(agent_instance_id),
            "agent_version": agent_version,
            "protocol_version": protocol_version,
            "openclaw_status": openclaw_status,
            "openclaw_error": openclaw_error,
            "command_result": command_result,
        }
        if openclaw_version is not None:
            # Underlying OpenClaw runtime version (image tag baked at build), surfaced
            # in the cloud agent-info UI. Optional for older cloud backends.
            body["openclaw_version"] = openclaw_version
        if browser is not None:
            body["browser"] = browser
        if agent_activity is not None:
            body["agent_activity"] = agent_activity
        if config_version is not None:
            # Manifest version the agent currently has applied (meta.configVersion of the
            # active openclaw.json). Lets the cloud detect and re-push undelivered changes.
            body["config_version"] = config_version
        if whatsapp is not None:
            # Last observed state of the WhatsApp channel (linked / connected / own number).
            # Absent while the channel is not configured or nothing has been observed yet.
            body["whatsapp"] = whatsapp
        if ai_subscription is not None:
            # The owner's own ChatGPT / Claude plan powering the agent (provider, whether it can work,
            # usage windows). Absent = the agent runs on credits. Never carries identity.
            body["ai_subscription"] = ai_subscription
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

    async def disconnect(
        self,
        *,
        agent_instance_id: UUID,
        revoke_tokens: bool = True,
    ) -> None:
        status, data = await self._request_json(
            "POST",
            "/agent/connection/disconnect",
            json_body={
                "agent_instance_id": str(agent_instance_id),
                "revoke_tokens": revoke_tokens,
            },
        )
        if status == 401:
            raise CloudAuthError(self._detail_message(data), status_code=401)
        if status >= 400:
            raise CloudConnectionError(self._detail_message(data))
