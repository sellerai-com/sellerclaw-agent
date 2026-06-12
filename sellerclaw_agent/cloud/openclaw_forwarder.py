"""POST chat inbound payloads to the local OpenClaw sellerclaw-ui gateway."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from sellerclaw_agent.http_clients import async_client

_log = logging.getLogger(__name__)

INBOUND_FORWARD_TIMEOUT = httpx.Timeout(60.0, connect=2.0)


def openclaw_gateway_base_url() -> str:
    """Base URL for OpenClaw HTTP gateway (host port mapped from the runtime container)."""
    explicit = (os.environ.get("OPENCLAW_GATEWAY_HTTP_BASE") or "").strip().rstrip("/")
    if explicit:
        return explicit
    port = int((os.environ.get("OPENCLAW_PORT_GATEWAY") or "7788").strip() or "7788")
    return f"http://127.0.0.1:{port}"


class LocalOpenClawForwarder:
    """Forward ``user_message`` SSE payloads to ``/api/channels/sellerclaw-ui/inbound``."""

    def __init__(
        self,
        *,
        base_url: str,
        hooks_token: str,
        gateway_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        # Inbound/abort go through OpenClaw's gateway-authenticated plugin routes
        # (``/api/channels/...`` + gateway token): gateway auth is what grants the
        # agent run ``operator.write`` so ``sessions_spawn`` works. ``/hooks/agent``
        # keeps its own hooks-token auth.
        self._gateway_token = gateway_token
        self._hooks_token = hooks_token
        self._transport = transport
        self._http = http_client

    async def post_inbound_json(self, body: dict[str, Any]) -> None:
        """POST ``body`` to the OpenClaw sellerclaw-ui inbound channel.

        Raises:
            httpx.ConnectError: the local gateway is not listening (stopped /
                not yet ready). Callers typically treat this as a silent drop.
            httpx.TimeoutException: connect/read timeout talking to the gateway.
            httpx.HTTPStatusError: gateway responded with a non-2xx status.
        """
        url = f"{self._base}/api/channels/sellerclaw-ui/inbound"
        headers = {
            "Authorization": f"Bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        if self._http is not None:
            response = await self._http.post(url, headers=headers, json=body)
        else:
            async with async_client(
                timeout=INBOUND_FORWARD_TIMEOUT,
                transport=self._transport,
            ) as client:
                response = await client.post(url, headers=headers, json=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "openclaw_inbound_forward_failed status=%s body=%s",
                exc.response.status_code,
                (exc.response.text or "")[:500],
            )
            raise

    async def post_abort_json(self, body: dict[str, Any]) -> None:
        """POST ``body`` to the OpenClaw sellerclaw-ui abort channel.

        Tells the plugin to abort the in-flight OpenClaw run for the chat's session.

        Raises:
            httpx.ConnectError: the local gateway is not listening.
            httpx.TimeoutException: connect/read timeout talking to the gateway.
            httpx.HTTPStatusError: gateway responded with a non-2xx status.
        """
        url = f"{self._base}/api/channels/sellerclaw-ui/abort"
        headers = {
            "Authorization": f"Bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        if self._http is not None:
            response = await self._http.post(url, headers=headers, json=body)
        else:
            async with async_client(
                timeout=INBOUND_FORWARD_TIMEOUT,
                transport=self._transport,
            ) as client:
                response = await client.post(url, headers=headers, json=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "openclaw_abort_forward_failed status=%s body=%s",
                exc.response.status_code,
                (exc.response.text or "")[:500],
            )
            raise

    async def post_hooks_agent_json(self, body: dict[str, Any]) -> None:
        """POST ``body`` to OpenClaw ``/hooks/agent`` (cloud-originated hook delivery)."""
        url = f"{self._base}/hooks/agent"
        headers = {
            "Authorization": f"Bearer {self._hooks_token}",
            "Content-Type": "application/json",
        }
        if self._http is not None:
            response = await self._http.post(url, headers=headers, json=body)
        else:
            async with async_client(
                timeout=INBOUND_FORWARD_TIMEOUT,
                transport=self._transport,
            ) as client:
                response = await client.post(url, headers=headers, json=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "openclaw_hooks_forward_failed status=%s body=%s",
                exc.response.status_code,
                (exc.response.text or "")[:500],
            )
            raise
