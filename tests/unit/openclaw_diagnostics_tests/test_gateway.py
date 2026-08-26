from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from openclaw_diagnostics.gateway import (
    PROTOCOL_VERSION,
    GatewayConnection,
    GatewayError,
    agent_id_from_session_key,
    gateway_ws_url,
    resolve_gateway_token,
)

pytestmark = pytest.mark.unit


class _FakeSocket:
    """A scripted WebSocket: replies are produced from the frames the client sends."""

    def __init__(self, *, challenge: bool = True, responder: Any = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._inbox: list[str] = []
        self._responder = responder
        self.closed = False
        if challenge:
            self._inbox.append(
                json.dumps(
                    {
                        "type": "event",
                        "event": "connect.challenge",
                        "payload": {"nonce": "n", "ts": 1},
                    }
                )
            )

    def push(self, frame: dict[str, Any]) -> None:
        self._inbox.append(json.dumps(frame))

    async def send(self, raw: str) -> None:
        frame = json.loads(raw)
        self.sent.append(frame)
        if frame.get("method") == "connect":
            self.push(
                {
                    "type": "res",
                    "id": frame["id"],
                    "ok": True,
                    "payload": {"type": "hello-ok", "protocol": PROTOCOL_VERSION},
                }
            )
        elif self._responder is not None:
            for reply in self._responder(frame):
                self.push(reply)

    async def recv(self) -> str:
        if not self._inbox:
            raise ConnectionError("no more frames")
        return self._inbox.pop(0)

    async def close(self) -> None:
        self.closed = True


def _connect_with(socket: _FakeSocket, monkeypatch: pytest.MonkeyPatch) -> GatewayConnection:
    async def _fake_connect(*_args: Any, **_kwargs: Any) -> _FakeSocket:
        return socket

    monkeypatch.setattr("openclaw_diagnostics.gateway.websockets.connect", _fake_connect)
    return GatewayConnection(url="ws://127.0.0.1:7789", token="t0ken")


async def test_handshake_declares_a_local_backend_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """These exact values are what exempt a loopback client from device pairing."""
    socket = _FakeSocket()

    async with _connect_with(socket, monkeypatch):
        pass

    connect = socket.sent[0]
    assert connect["method"] == "connect"
    params = connect["params"]
    assert params["client"]["id"] == "gateway-client"
    assert params["client"]["mode"] == "backend"
    assert params["minProtocol"] == PROTOCOL_VERSION
    assert params["maxProtocol"] == PROTOCOL_VERSION
    assert params["auth"] == {"token": "t0ken"}
    assert params["scopes"] == ["operator.read"]
    # Caps describe node-role approval surfaces; an operator client declares none, and an
    # empty list keeps a future cap from quietly narrowing what the broadcast sends us.
    assert params["caps"] == []


async def test_call_returns_the_matching_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def responder(frame: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "res", "id": frame["id"], "ok": True, "payload": {"sessions": []}}]

    socket = _FakeSocket(responder=responder)

    async with _connect_with(socket, monkeypatch) as conn:
        assert await conn.call("sessions.list", {"limit": 5}) == {"sessions": []}


async def test_call_raises_on_a_refused_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    def responder(frame: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "type": "res",
                "id": frame["id"],
                "ok": False,
                "error": {"code": "FORBIDDEN", "message": "missing scope: operator.read"},
            }
        ]

    socket = _FakeSocket(responder=responder)

    async with _connect_with(socket, monkeypatch) as conn:
        with pytest.raises(GatewayError, match="FORBIDDEN"):
            await conn.call("sessions.list")


async def test_events_arriving_before_a_response_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A busy gateway interleaves events with replies; neither may swallow the other."""

    def responder(frame: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"type": "event", "event": "session.message", "payload": {"sessionKey": "s1"}},
            {"type": "res", "id": frame["id"], "ok": True, "payload": {"subscribed": True}},
        ]

    socket = _FakeSocket(responder=responder)

    async with _connect_with(socket, monkeypatch) as conn:
        assert await conn.call("sessions.subscribe", {}) == {"subscribed": True}
        seen = [event async for event in _take(conn.events(), 1)]

    assert seen[0]["event"] == "session.message"


async def test_unreachable_gateway_raises_gateway_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("Connection refused")

    monkeypatch.setattr("openclaw_diagnostics.gateway.websockets.connect", _refuse)

    with pytest.raises(GatewayError, match="gateway unreachable"):
        async with GatewayConnection(url="ws://127.0.0.1:7789", token="t"):
            pass


async def test_dropped_connection_raises_gateway_error(monkeypatch: pytest.MonkeyPatch) -> None:
    socket = _FakeSocket(responder=lambda _frame: [])

    async with _connect_with(socket, monkeypatch) as conn:
        with pytest.raises(GatewayError, match="connection lost"):
            await conn.call("sessions.list")


async def test_token_read_from_the_config_the_gateway_itself_uses(tmp_path: Path) -> None:
    config = tmp_path / "openclaw.json"
    config.write_text(
        json.dumps({"gateway": {"auth": {"mode": "token", "token": "from-config"}}}),
        encoding="utf-8",
    )

    env = {"OPENCLAW_CONFIG_PATH": str(config)}
    assert resolve_gateway_token(env) == "from-config"


async def test_token_env_overrides_config(tmp_path: Path) -> None:
    config = tmp_path / "openclaw.json"
    config.write_text(json.dumps({"gateway": {"auth": {"token": "from-config"}}}), encoding="utf-8")

    env = {"OPENCLAW_CONFIG_PATH": str(config), "OPENCLAW_GATEWAY_TOKEN": "from-env"}
    assert resolve_gateway_token(env) == "from-env"


@pytest.mark.parametrize(
    "config_text",
    [
        pytest.param("{ not json", id="malformed"),
        pytest.param(json.dumps({"gateway": {}}), id="no-auth-block"),
        pytest.param(json.dumps({"gateway": {"auth": {"mode": "none"}}}), id="auth-without-token"),
    ],
)
async def test_token_is_none_when_config_has_no_usable_value(
    tmp_path: Path,
    config_text: str,
) -> None:
    config = tmp_path / "openclaw.json"
    config.write_text(config_text, encoding="utf-8")

    assert resolve_gateway_token({"OPENCLAW_CONFIG_PATH": str(config)}) is None


@pytest.mark.parametrize(
    ("session_key", "expected"),
    [
        pytest.param("agent:supervisor:sellerclaw-ui:direct:c1", "supervisor", id="direct-chat"),
        pytest.param("agent:sellercart:subagent:42", "sellercart", id="subagent"),
        pytest.param("global", "unknown", id="global"),
        pytest.param("agent::rest", "unknown", id="empty-agent-part"),
        pytest.param("agent:supervisor:", "unknown", id="no-rest"),
        pytest.param(None, "unknown", id="none"),
    ],
)
def test_agent_id_parsed_from_session_key(session_key: str | None, expected: str) -> None:
    assert agent_id_from_session_key(session_key) == expected


def test_ws_url_defaults_to_the_loopback_gateway_port() -> None:
    assert gateway_ws_url({}) == "ws://127.0.0.1:7789"
    assert gateway_ws_url({"OPENCLAW_PORT_GATEWAY_LOCAL": "9000"}) == "ws://127.0.0.1:9000"
    assert gateway_ws_url({"OPENCLAW_GATEWAY_WS_URL": "ws://host:1/x"}) == "ws://host:1/x"


async def _take(iterator: AsyncIterator[Any], count: int) -> AsyncIterator[Any]:
    taken = 0
    async for item in iterator:
        yield item
        taken += 1
        if taken >= count:
            return
