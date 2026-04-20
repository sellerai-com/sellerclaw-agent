from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudConnectionError
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url

_DEFAULT_TIMEOUT = httpx.Timeout(10.0)


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    refresh_token: str
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
    """Parsed /auth/device/token response."""

    pending: bool
    error: str | None
    login: LoginResult | None


class SellerClawAuthClient:
    """HTTP client for SellerClaw public auth endpoints."""

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
            if isinstance(detail, list):
                parts: list[str] = []
                for item in detail:
                    if isinstance(item, dict) and "msg" in item:
                        parts.append(str(item["msg"]))
                    else:
                        parts.append(str(item))
                return "; ".join(parts) if parts else "Authentication failed"
        return "Authentication failed"

    async def login(self, *, email: str, password: str) -> LoginResult:
        url = f"{self._base_url}/auth/login"
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

        access = data.get("access_token")
        refresh = data.get("refresh_token")
        user_raw = data.get("user")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise CloudConnectionError("Login response missing tokens")
        if not isinstance(user_raw, dict):
            raise CloudConnectionError("Login response missing user")

        user_id_raw = user_raw.get("id")
        email_raw = user_raw.get("email")
        name_raw = user_raw.get("name")
        if user_id_raw is None or not isinstance(email_raw, str):
            raise CloudConnectionError("Login response missing user id or email")

        user_name = str(name_raw) if name_raw is not None else ""
        return LoginResult(
            access_token=access,
            refresh_token=refresh,
            user_id=UUID(str(user_id_raw)),
            user_email=email_raw,
            user_name=user_name,
        )

    def _login_result_from_auth_payload(self, data: dict[str, Any]) -> LoginResult:
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        user_raw = data.get("user")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise CloudConnectionError("Device token response missing tokens")
        if not isinstance(user_raw, dict):
            raise CloudConnectionError("Device token response missing user")

        user_id_raw = user_raw.get("id")
        email_raw = user_raw.get("email")
        name_raw = user_raw.get("name")
        if user_id_raw is None or not isinstance(email_raw, str):
            raise CloudConnectionError("Device token response missing user id or email")

        user_name = str(name_raw) if name_raw is not None else ""
        return LoginResult(
            access_token=access,
            refresh_token=refresh,
            user_id=UUID(str(user_id_raw)),
            user_email=email_raw,
            user_name=user_name,
        )

    async def request_device_code(self) -> DeviceCodeResult:
        url = f"{self._base_url}/auth/device/code"
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
        url = f"{self._base_url}/auth/device/token"
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
            return DeviceTokenPollResult(pending=True, error=None, login=None)
        if isinstance(err, str) and err:
            return DeviceTokenPollResult(pending=False, error=err, login=None)

        if "access_token" in data and "refresh_token" in data:
            return DeviceTokenPollResult(
                pending=False,
                error=None,
                login=self._login_result_from_auth_payload(data),
            )

        return DeviceTokenPollResult(pending=False, error="invalid_device_code", login=None)

    async def refresh(self, *, refresh_token: str) -> str:
        url = f"{self._base_url}/auth/refresh"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    url,
                    json={"refresh_token": refresh_token},
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
            raise CloudConnectionError("Invalid JSON in refresh response") from exc
        if not isinstance(data, dict):
            raise CloudConnectionError("Refresh response must be a JSON object")
        access = data.get("access_token")
        if not isinstance(access, str):
            raise CloudConnectionError("Refresh response missing access_token")
        return access
