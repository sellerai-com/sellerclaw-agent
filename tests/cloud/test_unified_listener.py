from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx
import pytest

from sellerclaw_agent.cloud import chat_listener as cl
from sellerclaw_agent.cloud import unified_listener as ul
from sellerclaw_agent.cloud.exceptions import CloudConnectionError
from sellerclaw_agent.cloud.openclaw_forwarder import LocalOpenClawForwarder

pytestmark = pytest.mark.unit


@dataclass
class _FakeSupervisor:
    status: str
    error: str | None = None
    calls: int = 0

    def probe_openclaw_status(self) -> tuple[str, str | None]:
        self.calls += 1
        return self.status, self.error


def _sse_bytes(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    chunks: list[str] = []
    for name, data in events:
        chunks.append(f"event: {name}\n")
        chunks.append(f"data: {json.dumps(data)}\n")
        chunks.append("\n")
    return "".join(chunks).encode("utf-8")


class _GatewayRecorder:
    """Records every OpenClaw gateway request (path + json body)."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        self.calls.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(202)


async def _run_unified_consume(
    *,
    sse_body: bytes,
    supervisor: _FakeSupervisor,
    gateway: _GatewayRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://cloud.test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/stream"
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    sse_transport = httpx.MockTransport(handler)
    orig_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        if "transport" not in kwargs:
            kwargs["transport"] = sse_transport
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(ul.httpx, "AsyncClient", patched_async_client)

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as gw_http:
        forwarder = LocalOpenClawForwarder(base_url="http://gw.test", hooks_token="tok", http_client=gw_http)
        await ul._consume_unified_sse(
            agent_token="sca_access",
            agent_instance_id=uuid4(),
            forwarder=forwarder,
            supervisor_mgr=supervisor,  # type: ignore[arg-type]
            dedup=cl._MessageIdDedup(),
            stop=asyncio.Event(),
        )


@pytest.mark.asyncio
async def test_unified_routes_user_message_hook_and_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """One stream feeds all three event kinds to their respective OpenClaw endpoints."""
    body = _sse_bytes(
        [
            ("user_message", {"chat_id": "c1", "agent_id": "supervisor", "user_id": "u1", "text": "hi", "message_id": "m1"}),
            ("hook_event", {"kind": "tool_activity", "marker": "hookmark"}),
            ("cancel", {"chat_id": "c1", "agent_id": "supervisor", "message_id": "m1"}),
        ]
    )
    gateway = _GatewayRecorder()
    await _run_unified_consume(
        sse_body=body,
        supervisor=_FakeSupervisor(status="running"),
        gateway=gateway,
        monkeypatch=monkeypatch,
    )

    # user_message → inbound POST carrying the text
    assert any(call.get("text") == "hi" for call in gateway.calls), "user_message must reach inbound"
    # hook_event → hooks POST carrying the raw hook payload
    assert any(call.get("marker") == "hookmark" for call in gateway.calls), "hook_event must reach hooks"
    # cancel → abort POST with chat_id + agent_id (and no text)
    assert "/channels/sellerclaw-ui/abort" in gateway.paths, "cancel must reach the abort endpoint"
    assert {"chat_id": "c1", "agent_id": "supervisor"} in gateway.calls


@pytest.mark.asyncio
async def test_unified_hook_event_dropped_when_openclaw_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hook_event is not forwarded while OpenClaw is down (shared readiness gate)."""
    gateway = _GatewayRecorder()
    await _run_unified_consume(
        sse_body=_sse_bytes([("hook_event", {"kind": "x"})]),
        supervisor=_FakeSupervisor(status="stopped"),
        gateway=gateway,
        monkeypatch=monkeypatch,
    )
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_unified_consume_403_forbidden_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare 403 maps to ``edge_sse_forbidden`` so the loop backs off like the legacy streams."""
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://cloud.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "nope"})

    transport = httpx.MockTransport(handler)
    orig_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(ul.httpx, "AsyncClient", patched_async_client)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(202))) as gw_http:
        forwarder = LocalOpenClawForwarder(base_url="http://gw.test", hooks_token="tok", http_client=gw_http)
        with pytest.raises(CloudConnectionError, match="edge_sse_forbidden"):
            await ul._consume_unified_sse(
                agent_token="sca_access",
                agent_instance_id=uuid4(),
                forwarder=forwarder,
                supervisor_mgr=_FakeSupervisor(status="running"),  # type: ignore[arg-type]
                dedup=cl._MessageIdDedup(),
                stop=asyncio.Event(),
            )
