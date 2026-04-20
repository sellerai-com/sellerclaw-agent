from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from sellerclaw_agent.cloud import chat_listener as cl
from sellerclaw_agent.cloud.openclaw_forwarder import LocalOpenClawForwarder

pytestmark = pytest.mark.unit


def test_jwt_expires_at_epoch_decodes() -> None:
    exp = int(time.time()) + 3600
    payload = json.dumps({"exp": exp}).encode("utf-8")
    b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    token = f"eyJhbGciOiJub25lIn0.{b64}.e30"
    assert cl._jwt_expires_at_epoch(token) == float(exp)


def test_jwt_expires_at_epoch_invalid_returns_none() -> None:
    assert cl._jwt_expires_at_epoch("not-a-jwt") is None
    assert cl._jwt_expires_at_epoch("") is None


@dataclass
class _FakeSupervisor:
    """Stub ``SupervisorContainerManager`` that returns a scripted probe tuple."""

    status: str
    error: str | None = None
    calls: int = 0

    def probe_openclaw_status(self) -> tuple[str, str | None]:
        self.calls += 1
        return self.status, self.error


def _sse_bytes(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    """Build a byte body with ``event:`` + ``data:`` frames separated by blank lines."""
    chunks: list[str] = []
    for name, data in events:
        chunks.append(f"event: {name}\n")
        chunks.append(f"data: {json.dumps(data)}\n")
        chunks.append("\n")
    return "".join(chunks).encode("utf-8")


def _chat_sse_transport(body: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/chat/stream"
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return httpx.MockTransport(handler)


class _InboundRecorder:
    """Inbound handler for OpenClaw gateway that records calls / can simulate failures."""

    def __init__(self, behavior: str = "ok") -> None:
        self.behavior = behavior
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(request.content.decode("utf-8")))
        if self.behavior == "connect_error":
            raise httpx.ConnectError("connection refused", request=request)
        if self.behavior == "timeout":
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(202)


async def _run_consume(
    *,
    sse_body: bytes,
    supervisor: _FakeSupervisor,
    inbound: _InboundRecorder,
    agent_instance_id: UUID,
    dedup: cl._MessageIdDedup | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://cloud.test")
    chat_transport = _chat_sse_transport(sse_body)

    orig_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        if "transport" not in kwargs:
            kwargs["transport"] = chat_transport
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(cl.httpx, "AsyncClient", patched_async_client)

    inbound_transport = httpx.MockTransport(inbound)
    async with httpx.AsyncClient(transport=inbound_transport) as inbound_http:
        forwarder = LocalOpenClawForwarder(
            base_url="http://gw.test",
            hooks_token="tok",
            http_client=inbound_http,
        )
        stop = asyncio.Event()
        await cl._consume_chat_sse(
            access_token="access",
            agent_instance_id=agent_instance_id,
            forwarder=forwarder,
            supervisor_mgr=supervisor,  # type: ignore[arg-type]
            dedup=dedup or cl._MessageIdDedup(),
            stop=stop,
        )


@pytest.mark.asyncio
async def test_user_message_not_posted_when_openclaw_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid = "m-1"
    payload = {
        "chat_id": "c1",
        "agent_id": "supervisor",
        "user_id": "u1",
        "text": "hi",
        "message_id": mid,
    }
    body = _sse_bytes([("user_message", payload)])
    supervisor = _FakeSupervisor(status="stopped", error=None)
    inbound = _InboundRecorder(behavior="ok")
    dedup = cl._MessageIdDedup()

    await _run_consume(
        sse_body=body,
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        dedup=dedup,
        monkeypatch=monkeypatch,
    )

    assert inbound.calls == [], "should not POST when OpenClaw is stopped"
    assert dedup.already_forwarded(mid) is False, "must not record dropped message"


@pytest.mark.asyncio
async def test_user_message_dropped_on_connect_error_not_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid = "m-2"
    payload = {
        "chat_id": "c1",
        "agent_id": "supervisor",
        "user_id": "u1",
        "text": "hi",
        "message_id": mid,
    }
    body = _sse_bytes([("user_message", payload)])
    supervisor = _FakeSupervisor(status="running", error=None)
    inbound = _InboundRecorder(behavior="connect_error")
    dedup = cl._MessageIdDedup()

    await _run_consume(
        sse_body=body,
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        dedup=dedup,
        monkeypatch=monkeypatch,
    )

    assert len(inbound.calls) == 1, "must attempt the POST exactly once"
    assert dedup.already_forwarded(mid) is False, "must not record on ConnectError"


@pytest.mark.asyncio
async def test_user_message_forwarded_on_running_then_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid = "m-3"
    payload = {
        "chat_id": "c1",
        "agent_id": "supervisor",
        "user_id": "u1",
        "text": "hi",
        "message_id": mid,
    }
    body = _sse_bytes([("user_message", payload)])
    supervisor = _FakeSupervisor(status="running", error=None)
    inbound = _InboundRecorder(behavior="ok")
    dedup = cl._MessageIdDedup()

    await _run_consume(
        sse_body=body,
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        dedup=dedup,
        monkeypatch=monkeypatch,
    )

    assert len(inbound.calls) == 1
    assert inbound.calls[0]["text"] == "hi"
    assert dedup.already_forwarded(mid) is True, "must record on successful POST"


@pytest.mark.asyncio
async def test_probe_ttl_caches_status_across_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _sse_bytes(
        [
            (
                "user_message",
                {"chat_id": "c1", "agent_id": "a", "user_id": "u1", "text": "x", "message_id": "i1"},
            ),
            (
                "user_message",
                {"chat_id": "c1", "agent_id": "a", "user_id": "u1", "text": "y", "message_id": "i2"},
            ),
            (
                "user_message",
                {"chat_id": "c1", "agent_id": "a", "user_id": "u1", "text": "z", "message_id": "i3"},
            ),
        ]
    )
    supervisor = _FakeSupervisor(status="stopped", error=None)
    inbound = _InboundRecorder(behavior="ok")

    await _run_consume(
        sse_body=body,
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        monkeypatch=monkeypatch,
    )

    assert inbound.calls == []
    assert supervisor.calls == 1, "probe must be cached within TTL across consecutive messages"
