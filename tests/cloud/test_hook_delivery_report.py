from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from sellerclaw_agent.cloud import hooks_listener as hl
from sellerclaw_agent.cloud.edge_sse_common import make_openclaw_gate
from sellerclaw_agent.cloud.openclaw_forwarder import LocalOpenClawForwarder

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


async def _forward(
    payload: dict[str, Any],
    *,
    monkeypatch: pytest.MonkeyPatch,
    gateway_status: int = 202,
    supervisor: _FakeSupervisor | None = None,
    agent_token: str | None = "sca_access",
    gateway: _GatewayRecorder | None = None,
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
