"""Minimal client for the OpenClaw gateway WebSocket control plane.

This is the supported way for a process outside OpenClaw to ask what its sessions are
doing: `docs/gateway/external-apps.md` names WebSocket + RPC as the integration path for
"a script, dashboard, CI job, or another process". We use it for two jobs that used to
read session files off disk — the activity probe the cloud pings with, and the session
event mirror that puts agent activity into container logs.

Both live in this package because it is the one importable from every entry point in the
image: the diagnostics commands run from ``openclaw_start`` with the system interpreter,
while the API server runs out of ``/app``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Protocol, Self

import websockets

# The gateway refuses a connect outside this range. A bump upstream is an explicit breaking
# change for third-party clients, so pin it and let a mismatch fail loudly on the smoke test
# rather than silently degrade.
PROTOCOL_VERSION: Final[int] = 4

# `client.id` + `client.mode` together are what exempt a loopback backend from device
# pairing (`shouldSkipLocalBackendSelfPairing`). Both values are wire constants
# (`GATEWAY_CLIENT_IDS.GATEWAY_CLIENT` / `GATEWAY_CLIENT_MODES.BACKEND`); changing either
# turns every connect into a pairing request nobody is there to approve.
_CLIENT_ID: Final[str] = "gateway-client"
_CLIENT_MODE: Final[str] = "backend"

_DEFAULT_GATEWAY_PORT: Final[int] = 7789
_DEFAULT_CONFIG_PATH: Final[str] = "/home/node/.openclaw/openclaw.json"

_CONNECT_TIMEOUT_S: Final[float] = 5.0
_CALL_TIMEOUT_S: Final[float] = 15.0

# The gateway closes a client whose buffer runs away, and most session events are sent
# `dropIfSlow`. A generous inbound queue keeps a brief formatting stall from costing events.
_MAX_QUEUE: Final[int] = 512


class GatewayError(RuntimeError):
    """The gateway refused a call, or the connection could not be established."""


class GatewayUnreachableError(GatewayError):
    """Nothing is listening on the gateway port.

    Worth its own type: a stopped agent is the ordinary reason for it, while every other
    gateway failure means the process answered and then something went wrong — which is a
    bug to chase rather than an expected state.
    """


class GatewayRpc(Protocol):
    """An open gateway connection callers can issue RPCs on."""

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any: ...

    async def __aenter__(self) -> GatewayRpc: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class GatewayStream(GatewayRpc, Protocol):
    """A gateway connection that also streams subscribed events."""

    def events(self) -> AsyncIterator[dict[str, Any]]: ...

    async def __aenter__(self) -> GatewayStream: ...


def agent_id_from_session_key(session_key: str | None) -> str:
    """Agent id embedded in an ``agent:<agentId>:<rest>`` session key, or ``unknown``.

    Session rows and some event payloads carry no agent field of their own — the id lives
    in the key, in the same format upstream's ``parseAgentSessionKey`` reads. Keys without
    the prefix (``global``, ``unknown``) have no owning agent.
    """
    parts = (session_key or "").split(":")
    if len(parts) >= 3 and parts[0] == "agent" and parts[1].strip() and parts[2].strip():
        return parts[1].strip()
    return "unknown"


def gateway_ws_url(env: Mapping[str, str] | None = None) -> str:
    """WebSocket URL of the local gateway control plane."""
    environ = os.environ if env is None else env
    explicit = (environ.get("OPENCLAW_GATEWAY_WS_URL") or "").strip()
    if explicit:
        return explicit
    raw_port = (environ.get("OPENCLAW_PORT_GATEWAY_LOCAL") or "").strip()
    port = int(raw_port) if raw_port.isdigit() else _DEFAULT_GATEWAY_PORT
    return f"ws://127.0.0.1:{port}"


def resolve_gateway_token(env: Mapping[str, str] | None = None) -> str | None:
    """Shared secret the gateway authenticates operator clients with.

    ``openclaw.json`` is the authority: the gateway reads its own token from there, so a
    value taken from the same file cannot drift from the one being checked. The env vars
    are for callers that never materialize a config (tests, local runs).
    """
    environ = os.environ if env is None else env
    for name in ("OPENCLAW_GATEWAY_TOKEN", "SELLERCLAW_GATEWAY_TOKEN"):
        token = (environ.get(name) or "").strip()
        if token:
            return token
    config_path = Path((environ.get("OPENCLAW_CONFIG_PATH") or "").strip() or _DEFAULT_CONFIG_PATH)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(config, dict):
        return None
    gateway = config.get("gateway")
    auth = gateway.get("auth") if isinstance(gateway, dict) else None
    token = auth.get("token") if isinstance(auth, dict) else None
    return token.strip() or None if isinstance(token, str) else None


class GatewayConnection:
    """One authenticated WebSocket session against the local gateway.

    Use it as an async context manager; the handshake runs on entry, so a connection that
    is handed to you is one that has already been accepted and scoped.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        token: str | None = None,
        scopes: tuple[str, ...] = ("operator.read",),
        client_version: str = "0",
    ) -> None:
        self._url = url or gateway_ws_url()
        self._token = token if token is not None else resolve_gateway_token()
        self._scopes = scopes
        self._client_version = client_version
        self._ws: websockets.ClientConnection | None = None
        self._events: list[dict[str, Any]] = []

    async def __aenter__(self) -> Self:
        try:
            self._ws = await websockets.connect(
                self._url,
                open_timeout=_CONNECT_TIMEOUT_S,
                close_timeout=_CONNECT_TIMEOUT_S,
                max_queue=_MAX_QUEUE,
            )
        except (OSError, websockets.WebSocketException, TimeoutError) as exc:
            raise GatewayUnreachableError(f"gateway unreachable at {self._url}: {exc}") from exc
        try:
            await self._handshake()
        except BaseException:
            await self.aclose()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def _handshake(self) -> None:
        """Wait for the pre-connect challenge, then send ``connect`` as the first frame."""
        # The challenge only matters to device-auth clients (they sign its `ts`), but it
        # arrives before the socket is usable either way, so read past it rather than
        # letting it surface as an unexpected first frame to a caller.
        try:
            async with asyncio.timeout(_CONNECT_TIMEOUT_S):
                while True:
                    frame = await self._recv()
                    if frame.get("type") == "event" and frame.get("event") == "connect.challenge":
                        break
                    self._stash_event(frame)
        except TimeoutError as exc:
            raise GatewayError("gateway sent no connect challenge") from exc
        auth = {"token": self._token} if self._token else {}
        payload = await self.call(
            "connect",
            {
                "minProtocol": PROTOCOL_VERSION,
                "maxProtocol": PROTOCOL_VERSION,
                "client": {
                    "id": _CLIENT_ID,
                    "mode": _CLIENT_MODE,
                    "version": self._client_version,
                    "platform": "linux",
                },
                "role": "operator",
                "scopes": list(self._scopes),
                # No `caps`: in this OpenClaw version caps only describe node-role approval
                # surfaces; operator event delivery is scope-gated, and `operator.read` above
                # is what admits the all-session streams the mirror lives on. Keep the list
                # empty so no future cap can quietly narrow what the broadcast sends us.
                "caps": [],
                "auth": auth,
                "userAgent": f"sellerclaw-agent/{self._client_version}",
            },
        )
        if not isinstance(payload, dict) or payload.get("type") != "hello-ok":
            raise GatewayError(f"unexpected connect response: {str(payload)[:200]}")

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Issue one RPC and return its payload, stashing any events that arrive first."""
        ws = self._ws
        if ws is None:
            raise GatewayError("gateway connection is closed")
        request_id = uuid.uuid4().hex
        frame = {"type": "req", "id": request_id, "method": method, "params": params or {}}
        try:
            await ws.send(json.dumps(frame))
        except (OSError, websockets.WebSocketException) as exc:
            raise GatewayError(f"gateway send failed: {exc}") from exc
        # Bound the whole exchange, not each frame: an event storm must not let a probe
        # wait on its answer indefinitely.
        try:
            async with asyncio.timeout(_CALL_TIMEOUT_S):
                while True:
                    message = await self._recv()
                    if message.get("type") != "res" or message.get("id") != request_id:
                        self._stash_event(message)
                        continue
                    if message.get("ok"):
                        return message.get("payload")
                    error = message.get("error")
                    code = error.get("code") if isinstance(error, dict) else None
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise GatewayError(f"{method} failed: {code or 'ERROR'}: {detail}")
        except TimeoutError as exc:
            raise GatewayError(f"{method} timed out after {_CALL_TIMEOUT_S:g}s") from exc

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield event frames as they arrive, oldest stashed ones first."""
        while self._events:
            yield self._events.pop(0)
        while True:
            frame = await self._recv()
            if frame.get("type") == "event":
                yield frame

    def _stash_event(self, frame: dict[str, Any]) -> None:
        if frame.get("type") == "event" and len(self._events) < _MAX_QUEUE:
            self._events.append(frame)

    async def _recv(self) -> dict[str, Any]:
        ws = self._ws
        if ws is None:
            raise GatewayError("gateway connection is closed")
        try:
            raw = await ws.recv()
        except (OSError, websockets.WebSocketException) as exc:
            raise GatewayError(f"gateway connection lost: {exc}") from exc
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            frame = json.loads(raw)
        except ValueError as exc:
            raise GatewayError(f"malformed gateway frame: {raw[:200]}") from exc
        if not isinstance(frame, dict):
            raise GatewayError(f"unexpected gateway frame: {raw[:200]}")
        return frame
