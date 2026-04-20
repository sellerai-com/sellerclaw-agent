"""POST chat inbound payloads to the local OpenClaw sellerclaw-ui gateway."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

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
    """Forward ``user_message`` SSE payloads to ``/channels/sellerclaw-ui/inbound``."""

    def __init__(
        self,
        *,
        base_url: str,
        hooks_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = hooks_token
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
        url = f"{self._base}/channels/sellerclaw-ui/inbound"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if self._http is not None:
            response = await self._http.post(url, headers=headers, json=body)
        else:
            async with httpx.AsyncClient(
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
