from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudConnectionError
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url

_DEFAULT_TIMEOUT = httpx.Timeout(10.0)


@dataclass(frozen=True)
class AgentAuthResult:
    """Successful agent login (password, device poll, etc.): opaque ``sca_`` token + user."""

    agent_token: str
    user_id: UUID
    user_email: str
    user_name: str


@dataclass(frozen=True)
class DeviceCodeResult:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class DeviceTokenPollResult:
    """Parsed ``/agent/auth/device/token`` response."""

    pending: bool
    error: str | None
    auth: AgentAuthResult | None


class SellerClawAuthClient:
    """HTTP client for SellerClaw **agent** auth endpoints (``/agent/auth/*``)."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url if base_url is not None else get_sellerclaw_api_url()).rstrip("/")
        self._timeout = timeout or _DEFAULT_TIMEOUT
        self._transport = transport

    def _auth_error_message(self, payload: Any) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str):
                return detail
            if isinstance(detail, dict):
                msg = detail.get("message")
                if isinstance(msg, str):
                    return msg
            if isinstance(detail, list):
                parts: list[str] = []
                for item in detail:
                    if isinstance(item, dict) and "msg" in item:
                        parts.append(str(item["msg"]))
                    else:
                        parts.append(str(item))
                return "; ".join(parts) if parts else "Authentication failed"
        return "Authentication failed"

    def _agent_auth_from_payload(self, data: dict[str, Any]) -> AgentAuthResult:
        token = data.get("agent_token")
        user_raw = data.get("user")
        if not isinstance(token, str) or not token.strip():
            raise CloudConnectionError("Auth response missing agent_token")
        if not isinstance(user_raw, dict):
            raise CloudConnectionError("Auth response missing user")

        user_id_raw = user_raw.get("id")
        email_raw = user_raw.get("email")
        name_raw = user_raw.get("name")
        if user_id_raw is None or not isinstance(email_raw, str):
            raise CloudConnectionError("Auth response missing user id or email")

        user_name = str(name_raw) if name_raw is not None else ""
        return AgentAuthResult(
            agent_token=token.strip(),
            user_id=UUID(str(user_id_raw)),
            user_email=email_raw,
            user_name=user_name,
        )

    async def login(self, *, email: str, password: str) -> AgentAuthResult:
        url = f"{self._base_url}/agent/auth/token"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    url,
                    json={"email": email, "password": password},
                    headers={"Content-Type": "application/json"},
                )
        except httpx.RequestError as exc:
            raise CloudConnectionError(str(exc)) from exc

        if response.status_code >= 500:
            raise CloudConnectionError(
                f"SellerClaw server error: HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            raise CloudAuthError(
                self._auth_error_message(payload),
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise CloudConnectionError("Invalid JSON in login response") from exc
        if not isinstance(data, dict):
            raise CloudConnectionError("Login response must be a JSON object")
        return self._agent_auth_from_payload(data)

    async def request_device_code(self) -> DeviceCodeResult:
        url = f"{self._base_url}/agent/auth/device/code"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(url, headers={"Content-Type": "application/json"})
        except httpx.RequestError as exc:
            raise CloudConnectionError(str(exc)) from exc

        if response.status_code >= 500:
            raise CloudConnectionError(
                f"SellerClaw server error: HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            raise CloudAuthError(
                self._auth_error_message(payload),
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise CloudConnectionError("Invalid JSON in device code response") from exc
        if not isinstance(data, dict):
            raise CloudConnectionError("Device code response must be a JSON object")

        dc = data.get("device_code")
        uc = data.get("user_code")
        vu = data.get("verification_uri")
        exp_raw = data.get("expires_in")
        interval_raw = data.get("interval")
        if not isinstance(dc, str) or not isinstance(uc, str) or not isinstance(vu, str):
            raise CloudConnectionError("Device code response missing required fields")
        try:
            exp_i = int(exp_raw)  # type: ignore[arg-type]
            interval_i = int(interval_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise CloudConnectionError("Device code response missing required fields") from None

        return DeviceCodeResult(
            device_code=dc,
            user_code=uc,
            verification_uri=vu,
            expires_in=exp_i,
            interval=interval_i,
        )

    async def poll_device_token(self, *, device_code: str) -> DeviceTokenPollResult:
        url = f"{self._base_url}/agent/auth/device/token"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    url,
                    json={"device_code": device_code},
                    headers={"Content-Type": "application/json"},
                )
        except httpx.RequestError as exc:
            raise CloudConnectionError(str(exc)) from exc

        if response.status_code >= 500:
            raise CloudConnectionError(
                f"SellerClaw server error: HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            raise CloudAuthError(
                self._auth_error_message(payload),
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise CloudConnectionError("Invalid JSON in device token response") from exc
        if not isinstance(data, dict):
            raise CloudConnectionError("Device token response must be a JSON object")

        err = data.get("error")
        if err == "authorization_pending":
            return DeviceTokenPollResult(pending=True, error=None, auth=None)
        if isinstance(err, str) and err:
            return DeviceTokenPollResult(pending=False, error=err, auth=None)

        if "agent_token" in data and "user" in data:
            return DeviceTokenPollResult(
                pending=False,
                error=None,
                auth=self._agent_auth_from_payload(data),
            )

        return DeviceTokenPollResult(pending=False, error="invalid_device_code", auth=None)
