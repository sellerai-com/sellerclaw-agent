from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from sellerclaw_agent.cloud import hooks_listener as hl
from sellerclaw_agent.cloud.edge_sse_common import make_openclaw_gate
from sellerclaw_agent.cloud.openclaw_forwarder import LocalOpenClawForwarder
from openclaw_diagnostics.gateway import GatewayError, GatewayUnreachableError

pytestmark = pytest.mark.unit


@dataclass
class _FakeSupervisor:
    status: str = "running"
    error: str | None = None

    def probe_openclaw_status(self) -> tuple[str, str | None]:
        return self.status, self.error


class _CloudRecorder:
    """Every delivery report the agent sent back to the cloud."""

    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.reports: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/hooks/delivery"
        self.reports.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(self.status, json={"reopened": 0})


class _GatewayRecorder:
    """Every request the agent made to the local OpenClaw gateway."""

    def __init__(self, *, status: int = 202) -> None:
        self.status = status
        self.paths: list[str] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        self.bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(self.status, text="admission timeout")


class _SendGateway:
    """The gateway's WebSocket RPC as the ``send`` envelope uses it: records calls, or refuses."""

    def __init__(self, *, error: str | None = None, unreachable: bool = False) -> None:
        self.error = error
        self.unreachable = unreachable
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    def __call__(self) -> _SendGateway:
        return self

    async def __aenter__(self) -> _SendGateway:
        if self.unreachable:
            raise GatewayUnreachableError("gateway unreachable at ws://127.0.0.1:7789")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def call(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        self.calls.append((method, params or {}, timeout))
        if self.error is not None:
            raise GatewayError(f"{method} failed: UNAVAILABLE: {self.error}")
        return {"runId": (params or {}).get("idempotencyKey"), "messageId": "m1", "channel": "whatsapp"}


async def _forward(
    payload: dict[str, Any],
    *,
    monkeypatch: pytest.MonkeyPatch,
    gateway_status: int = 202,
    supervisor: _FakeSupervisor | None = None,
    agent_token: str | None = "sca_access",
    gateway: _GatewayRecorder | None = None,
    send_gateway: _SendGateway | None = None,
) -> list[dict[str, Any]]:
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://cloud.test")
    cloud = _CloudRecorder()

    def _cloud_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(cloud)
        return httpx.AsyncClient(*args, **kwargs)

    monkeypatch.setattr(hl, "async_client", _cloud_client)

    recorder = gateway or _GatewayRecorder(status=gateway_status)
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder)) as gw_http:
        forwarder = LocalOpenClawForwarder(
            base_url="http://gw.test",
            hooks_token="tok",
            gateway_token="gw-tok",
            http_client=gw_http,
        )
        await hl.forward_hook_event(
            payload,
            forwarder=forwarder,
            openclaw_gate=make_openclaw_gate(supervisor or _FakeSupervisor()),  # type: ignore[arg-type]
            agent_token=agent_token,
            send_gateway=send_gateway or _SendGateway(),
        )
    return cloud.reports


async def test_a_named_hook_that_lands_is_reported_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = await _forward(
        {"endpoint": "agent", "hookId": "h1", "body": {"message": "digest"}},
        monkeypatch=monkeypatch,
    )
    assert reports == [{"hook_id": "h1", "delivered": True, "error": None}]


async def test_an_envelope_is_unwrapped_before_it_reaches_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The envelope is for us; the gateway gets the body exactly as the cloud wrote it."""
    gateway = _GatewayRecorder()
    body = {
        "message": "digest",
        "agentId": "supervisor",
        "sessionKey": "agent:supervisor:sellerclaw-ui:direct:c1",
        "sessionMode": "persistent",
        "deliver": False,
    }
    reports = await _forward(
        {"endpoint": "agent", "hookId": "h9", "body": body},
        monkeypatch=monkeypatch,
        gateway=gateway,
    )

    assert gateway.paths == ["/hooks/agent"]
    assert gateway.bodies == [body]
    assert reports == [{"hook_id": "h9", "delivered": True, "error": None}]


async def test_the_gateway_event_queue_is_no_longer_an_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/hooks/wake`` accepts an event it may not run for minutes — so it is refused, not used."""
    gateway = _GatewayRecorder()
    reports = await _forward(
        {"endpoint": "wake", "hookId": "h10", "body": {"text": "digest"}},
        monkeypatch=monkeypatch,
        gateway=gateway,
    )

    assert gateway.paths == []
    assert reports[0]["delivered"] is False
    assert "unknown_endpoint" in (reports[0]["error"] or "")


async def test_a_plain_payload_still_goes_to_the_agent_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every hook that is not a wake — orders, media, email — is untouched by any of this."""
    gateway = _GatewayRecorder()
    await _forward({"message": "an order needs you"}, monkeypatch=monkeypatch, gateway=gateway)

    assert gateway.paths == ["/hooks/agent"]
    assert gateway.bodies == [{"message": "an order needs you"}]


async def test_an_endpoint_we_do_not_know_is_refused_rather_than_guessed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _GatewayRecorder()
    reports = await _forward(
        {"endpoint": "whatever", "hookId": "h8", "body": {"message": "x"}},
        monkeypatch=monkeypatch,
        gateway=gateway,
    )

    assert gateway.paths == [], "nothing reaches the gateway on an address we cannot verify"
    assert reports[0]["delivered"] is False
    assert "unknown_endpoint" in (reports[0]["error"] or "")


async def test_a_gateway_that_refuses_the_run_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact loss this exists for: the cloud counted admission timeouts as delivered."""
    reports = await _forward(
        {"endpoint": "agent", "hookId": "h2", "body": {"message": "digest"}},
        monkeypatch=monkeypatch,
        gateway_status=503,
    )
    assert len(reports) == 1
    assert reports[0]["hook_id"] == "h2"
    assert reports[0]["delivered"] is False
    assert "503" in (reports[0]["error"] or "")


async def test_a_hook_forwarded_while_openclaw_is_down_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = await _forward(
        {"endpoint": "agent", "hookId": "h3", "body": {"message": "digest"}},
        monkeypatch=monkeypatch,
        supervisor=_FakeSupervisor(status="stopped", error="container exited"),
    )
    assert len(reports) == 1
    assert reports[0]["delivered"] is False
    assert "openclaw_not_running" in (reports[0]["error"] or "")


@pytest.mark.parametrize(
    ("payload", "token", "case"),
    [
        pytest.param({"message": "x"}, "sca_access", "plain", id="hook-the-cloud-is-not-tracking"),
        pytest.param(
            {"endpoint": "agent", "hookId": "", "body": {"message": "x"}},
            "sca_access",
            "blank",
            id="blank-id",
        ),
        pytest.param(
            {"endpoint": "agent", "hookId": "h4", "body": {"message": "x"}},
            None,
            "no-token",
            id="no-agent-token",
        ),
    ],
)
async def test_untracked_hooks_are_forwarded_without_a_report(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], token: str | None, case: str
) -> None:
    """Most hooks carry no id — those behave exactly as they did before reporting existed."""
    _ = case
    assert await _forward(payload, monkeypatch=monkeypatch, agent_token=token) == []


_SEND_BODY = {
    "channel": "whatsapp",
    "to": "+15550002222",
    "message": "1 new order from Acme\n#1001 · 2 items",
    "agentId": "supervisor",
    "idempotencyKey": "msg-1",
}


async def test_a_send_goes_out_as_written_without_an_agent_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The text is the gateway's plain send — /hooks/agent would run a model on it instead."""
    gateway = _GatewayRecorder()
    send_gateway = _SendGateway()

    reports = await _forward(
        {"endpoint": "send", "hookId": "msg-1", "body": _SEND_BODY},
        monkeypatch=monkeypatch,
        gateway=gateway,
        send_gateway=send_gateway,
    )

    assert gateway.paths == [], "no agent turn"
    assert send_gateway.calls == [("send", _SEND_BODY, 30.0)]
    assert reports == [{"hook_id": "msg-1", "delivered": True, "error": None}]


async def test_a_send_passes_on_only_the_fields_the_cloud_may_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_gateway = _SendGateway()

    await _forward(
        {
            "endpoint": "send",
            "hookId": "msg-2",
            "body": {**_SEND_BODY, "mediaUrl": "https://evil.test/x.png", "sessionKey": "agent:other"},
        },
        monkeypatch=monkeypatch,
        send_gateway=send_gateway,
    )

    assert [params for _, params, _ in send_gateway.calls] == [_SEND_BODY]


@pytest.mark.parametrize(
    ("send_gateway", "body", "expected_error"),
    [
        pytest.param(
            _SendGateway(error="No active WhatsApp Web listener (account: default)."),
            _SEND_BODY,
            "No active WhatsApp Web listener",
            id="whatsapp-not-linked",
        ),
        pytest.param(
            _SendGateway(unreachable=True), _SEND_BODY, "gateway unreachable", id="gateway-down"
        ),
        pytest.param(
            _SendGateway(),
            {key: value for key, value in _SEND_BODY.items() if key != "to"},
            "send body is missing to",
            id="no-recipient",
        ),
    ],
)
async def test_a_send_that_does_not_go_out_is_reported_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
    send_gateway: _SendGateway,
    body: dict[str, Any],
    expected_error: str,
) -> None:
    reports = await _forward(
        {"endpoint": "send", "hookId": "msg-3", "body": body},
        monkeypatch=monkeypatch,
        send_gateway=send_gateway,
    )

    assert len(reports) == 1
    assert reports[0]["hook_id"] == "msg-3"
    assert reports[0]["delivered"] is False
    assert expected_error in (reports[0]["error"] or "")


async def test_a_send_while_openclaw_is_down_never_reaches_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_gateway = _SendGateway()

    reports = await _forward(
        {"endpoint": "send", "hookId": "msg-4", "body": _SEND_BODY},
        monkeypatch=monkeypatch,
        supervisor=_FakeSupervisor(status="stopped", error="container exited"),
        send_gateway=send_gateway,
    )

    assert send_gateway.calls == []
    assert reports[0]["delivered"] is False
    assert "openclaw_not_running" in (reports[0]["error"] or "")
