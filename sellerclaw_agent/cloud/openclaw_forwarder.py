"""POST chat inbound payloads to the local OpenClaw sellerclaw-ui gateway."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from sellerclaw_agent.http_clients import async_client

_log = logging.getLogger(__name__)

INBOUND_FORWARD_TIMEOUT = httpx.Timeout(60.0, connect=2.0)

#: The ``sellerclaw-ui`` plugin's cron routes (``plugins/sellerclaw-ui/src/cron-routes.ts``).
CRON_COMMAND_PATH = "/api/channels/sellerclaw-ui/cron/command"
CRON_STANDBY_PATH = "/api/channels/sellerclaw-ui/cron/standby"
STEER_PATH = "/api/channels/sellerclaw-ui/steer"
SESSION_QUESTION_PATH = "/api/channels/sellerclaw-ui/session/question"


def openclaw_gateway_base_url() -> str:
    """Base URL every in-container caller must use to reach the OpenClaw gateway.

    This is the gateway's own loopback listener, NOT the nginx port published to the host
    (``OPENCLAW_PORT_GATEWAY``, 7788). OpenClaw rejects a proxied request whose forwarded
    client address it cannot attribute to a real client -- HTTP 403
    ``proxy_attribution_required`` -- and a loopback -> loopback hop has no client to
    attribute. So anything inside the container talks to the gateway directly and leaves
    nginx to the traffic that genuinely arrives from outside.
    """
    explicit = (os.environ.get("OPENCLAW_GATEWAY_HTTP_BASE") or "").strip().rstrip("/")
    if explicit:
        return explicit
    port = int(
        (os.environ.get("OPENCLAW_PORT_GATEWAY_LOCAL") or "7789").strip() or "7789"
    )
    return f"http://127.0.0.1:{port}"


def _gateway_headers(token: str) -> dict[str, str]:
    """Headers for one request to the gateway, on a connection of its own.

    The gateway admits requests arriving on one connection strictly one after another: the next is
    not routed until the previous handler has *returned* (OpenClaw ``runHttpConnectionRequest``),
    and the chat handler returns only when its whole turn is over. On a kept-alive connection every
    message, stop and hook sent while a turn runs therefore waits for that turn — a minute or more
    — even though each is answered ``202`` the moment its own handler starts; past the read timeout
    the message is logged lost. ``Connection: close`` gives every request its own connection, so
    none queues behind another's turn. It is a loopback hop, so the handshake costs nothing.
    """
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Connection": "close",
    }


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
        # agent run its operator scopes (``sessions_spawn`` needs write, session reads
        # need read — the scope surface is set on the plugin's routes). ``/hooks/agent``
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
        headers = _gateway_headers(self._gateway_token)
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
        headers = _gateway_headers(self._gateway_token)
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

    async def post_cron_command_json(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST one ``cron_command`` frame to the plugin's cron command route; return its outcome.

        The plugin runs the command through the gateway's scheduler and answers 200 with
        ``{ok, error, store_epoch, job, run_id}`` — a command that failed included.

        Raises:
            httpx.ConnectError: the local gateway is not listening.
            httpx.TimeoutException: connect/read timeout talking to the gateway.
            httpx.HTTPStatusError: the route refused the frame (non-2xx).
            ValueError: the route answered something that is not a JSON object.
        """
        response = await self._post_gateway(CRON_COMMAND_PATH, body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "openclaw_cron_command_forward_failed status=%s body=%s",
                exc.response.status_code,
                (exc.response.text or "")[:500],
            )
            raise
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("cron command route answered a non-object")
        return payload

    async def post_steer_json(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST ``{sessionKey, text, idempotencyKey}`` to the plugin's steer route; return its outcome.

        The plugin hands the text to the session's active run and answers 200 with
        ``{steered: true, runId?}`` or ``{steered: false, reason}``.

        Raises:
            httpx.ConnectError: the local gateway is not listening.
            httpx.TimeoutException: connect/read timeout talking to the gateway.
            httpx.HTTPStatusError: the route refused the body (non-2xx), e.g. an older plugin.
            ValueError: the route answered something that is not a JSON object.
        """
        response = await self._post_gateway(STEER_PATH, body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "openclaw_steer_forward_failed status=%s body=%s",
                exc.response.status_code,
                (exc.response.text or "")[:500],
            )
            raise
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("steer route answered a non-object")
        return payload

    async def post_session_question_json(self, body: dict[str, Any], *, answer_timeout_s: float) -> dict[str, Any]:
        """POST ``{sessionKey, question, timeoutMs}`` to the plugin's session-question route.

        The plugin forks the session read-only and answers 200 with ``{ok: true, text, usage?}`` or
        ``{ok: false, error}``. The read timeout outlasts ``answer_timeout_s``, the fork's own budget.

        Raises:
            httpx.ConnectError: the local gateway is not listening.
            httpx.TimeoutException: connect/read timeout talking to the gateway.
            httpx.HTTPStatusError: the route refused the body (non-2xx), e.g. an older plugin.
            ValueError: the route answered something that is not a JSON object.
        """
        timeout = httpx.Timeout(answer_timeout_s + 15.0, connect=2.0)
        response = await self._post_gateway(SESSION_QUESTION_PATH, body, timeout=timeout)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "openclaw_session_question_forward_failed status=%s body=%s",
                exc.response.status_code,
                (exc.response.text or "")[:500],
            )
            raise
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("session question route answered a non-object")
        return payload

    async def post_cron_standby_json(self, *, standby: bool) -> tuple[bool, dict[str, Any]]:
        """Ask the plugin to enter (``True``) or leave (``False``) cron standby.

        Returns ``(held, payload)``: ``held`` is True once the plugin confirms the state (200); a
        503 means it could not yet (scheduler not ready, a job refused) and the caller retries.

        Raises:
            httpx.ConnectError / httpx.TimeoutException: the gateway is not reachable.
        """
        response = await self._post_gateway(CRON_STANDBY_PATH, {"standby": standby})
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return response.status_code == 200, payload if isinstance(payload, dict) else {}

    async def _post_gateway(
        self, path: str, body: dict[str, Any], *, timeout: httpx.Timeout | None = None
    ) -> httpx.Response:
        url = f"{self._base}{path}"
        headers = _gateway_headers(self._gateway_token)
        if self._http is not None:
            if timeout is None:
                return await self._http.post(url, headers=headers, json=body)
            return await self._http.post(url, headers=headers, json=body, timeout=timeout)
        async with async_client(
            timeout=timeout or INBOUND_FORWARD_TIMEOUT,
            transport=self._transport,
        ) as client:
            return await client.post(url, headers=headers, json=body)

    async def post_hooks_agent_json(self, body: dict[str, Any]) -> None:
        """POST ``body`` to OpenClaw ``/hooks/agent`` (cloud-originated hook delivery)."""
        url = f"{self._base}/hooks/agent"
        headers = _gateway_headers(self._hooks_token)
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
