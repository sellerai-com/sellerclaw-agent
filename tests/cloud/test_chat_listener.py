from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from sellerclaw_agent.cloud import chat_listener as cl
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import (
    CloudAgentSuspendedError,
    CloudConnectionError,
    CloudConnectionInactiveError,
    CloudSessionInvalidatedError,
)
from sellerclaw_agent.cloud.openclaw_forwarder import LocalOpenClawForwarder

pytestmark = pytest.mark.unit


def test_resolve_agent_bearer_prefers_file_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "sca_from_env")
    CredentialsStorage(tmp_path).save(
        user_id=UUID("35922ddf-4020-5179-b163-3d90bcb86b00"),
        user_email="a@b.c",
        user_name="A",
        agent_token="sca_from_file",
        connected_at="t",
    )
    assert resolve_agent_bearer_token(CredentialsStorage(tmp_path)) == "sca_from_file"


def test_resolve_agent_bearer_env_when_no_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_API_KEY", "sca_from_env")
    assert resolve_agent_bearer_token(CredentialsStorage(tmp_path)) == "sca_from_env"


def test_resolve_agent_bearer_none_without_file_or_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    assert resolve_agent_bearer_token(CredentialsStorage(tmp_path)) is None


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


def _chat_sse_transport_status(status_code: int, json_body: dict[str, Any] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent/chat/stream"
        if json_body is not None:
            return httpx.Response(status_code, json=json_body)
        return httpx.Response(status_code)

    return httpx.MockTransport(handler)


class _InboundRecorder:
    """Inbound handler for OpenClaw gateway that records calls / can simulate failures."""

    def __init__(self, behavior: str = "ok") -> None:
        self.behavior = behavior
        self.calls: list[dict[str, Any]] = []
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
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
            gateway_token="gw-tok",
            http_client=inbound_http,
        )
        stop = asyncio.Event()
        await cl._consume_chat_sse(
            agent_token="sca_access",
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
async def test_redelivery_bypasses_dedup_and_marks_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catch-up re-delivery of an already-forwarded id is still forwarded, tagged redelivery."""
    mid = "m-redel"
    payload = {
        "chat_id": "c1",
        "agent_id": "supervisor",
        "user_id": "u1",
        "text": "retry",
        "message_id": mid,
        "redelivery": True,
    }
    body = _sse_bytes([("user_message", payload)])
    supervisor = _FakeSupervisor(status="running", error=None)
    inbound = _InboundRecorder(behavior="ok")
    dedup = cl._MessageIdDedup()
    dedup.record_forwarded(mid)  # the earlier (lost) live delivery already recorded it

    await _run_consume(
        sse_body=body,
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        dedup=dedup,
        monkeypatch=monkeypatch,
    )

    assert len(inbound.calls) == 1, "re-delivery must be forwarded despite the dedup hit"
    assert inbound.calls[0]["redelivery"] is True
    assert inbound.calls[0]["text"] == "retry"


@pytest.mark.asyncio
async def test_non_redelivery_duplicate_is_dropped_by_dedup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live (non-redelivery) duplicate of an already-forwarded id is still suppressed."""
    mid = "m-dup"
    payload = {
        "chat_id": "c1",
        "agent_id": "supervisor",
        "user_id": "u1",
        "text": "dup",
        "message_id": mid,
    }
    body = _sse_bytes([("user_message", payload)])
    supervisor = _FakeSupervisor(status="running", error=None)
    inbound = _InboundRecorder(behavior="ok")
    dedup = cl._MessageIdDedup()
    dedup.record_forwarded(mid)

    await _run_consume(
        sse_body=body,
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        dedup=dedup,
        monkeypatch=monkeypatch,
    )

    assert inbound.calls == [], "a non-redelivery duplicate must be dropped by the dedup"


@pytest.mark.asyncio
async def test_consume_chat_sse_403_agent_suspended_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://cloud.test")
    transport = _chat_sse_transport_status(
        403,
        {"detail": {"code": "agent_suspended", "message": "paused"}},
    )
    orig_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(cl.httpx, "AsyncClient", patched_async_client)
    stop = asyncio.Event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(202))) as oc_http:
        forwarder = LocalOpenClawForwarder(base_url="http://gw.test", hooks_token="tok", gateway_token="gw-tok", http_client=oc_http)
        with pytest.raises(CloudAgentSuspendedError):
            await cl._consume_chat_sse(
                agent_token="sca_access",
                agent_instance_id=uuid4(),
                forwarder=forwarder,
                supervisor_mgr=_FakeSupervisor(status="running"),  # type: ignore[arg-type]
                dedup=cl._MessageIdDedup(),
                stop=stop,
            )


@pytest.mark.asyncio
async def test_consume_chat_sse_403_forbidden_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://cloud.test")
    transport = _chat_sse_transport_status(403, {"detail": "nope"})
    orig_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(cl.httpx, "AsyncClient", patched_async_client)
    stop = asyncio.Event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(202))) as oc_http:
        forwarder = LocalOpenClawForwarder(base_url="http://gw.test", hooks_token="tok", gateway_token="gw-tok", http_client=oc_http)
        with pytest.raises(CloudConnectionError, match="chat_sse_forbidden"):
            await cl._consume_chat_sse(
                agent_token="sca_access",
                agent_instance_id=uuid4(),
                forwarder=forwarder,
                supervisor_mgr=_FakeSupervisor(status="running"),  # type: ignore[arg-type]
                dedup=cl._MessageIdDedup(),
                stop=stop,
            )


@pytest.mark.parametrize(
    ("code", "expected_exc"),
    [
        pytest.param(
            "agent_session_invalidated",
            CloudSessionInvalidatedError,
            id="session-invalidated",
        ),
        pytest.param(
            "agent_connection_inactive",
            CloudConnectionInactiveError,
            id="connection-inactive",
        ),
        pytest.param(
            "agent_connection_not_found",
            CloudConnectionInactiveError,
            id="connection-not-found",
        ),
    ],
)
@pytest.mark.asyncio
async def test_consume_chat_sse_403_session_codes_raise_specific_errors(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    expected_exc: type[BaseException],
) -> None:
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://cloud.test")
    transport = _chat_sse_transport_status(
        403,
        {"detail": {"code": code, "message": "stale session"}},
    )
    orig_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(cl.httpx, "AsyncClient", patched_async_client)
    stop = asyncio.Event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(202))) as oc_http:
        forwarder = LocalOpenClawForwarder(base_url="http://gw.test", hooks_token="tok", gateway_token="gw-tok", http_client=oc_http)
        with pytest.raises(expected_exc):
            await cl._consume_chat_sse(
                agent_token="sca_access",
                agent_instance_id=uuid4(),
                forwarder=forwarder,
                supervisor_mgr=_FakeSupervisor(status="running"),  # type: ignore[arg-type]
                dedup=cl._MessageIdDedup(),
                stop=stop,
            )


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


@pytest.mark.asyncio
async def test_cancel_event_forwarded_to_abort_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``cancel`` SSE event POSTs chat_id+agent_id to the abort channel; dedup untouched."""
    supervisor = _FakeSupervisor(status="running")
    inbound = _InboundRecorder()
    dedup = cl._MessageIdDedup()
    await _run_consume(
        sse_body=_sse_bytes(
            [("cancel", {"chat_id": "c1", "agent_id": "supervisor", "message_id": "m1"})]
        ),
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        dedup=dedup,
        monkeypatch=monkeypatch,
    )
    assert inbound.paths == ["/api/channels/sellerclaw-ui/abort"]
    assert inbound.calls == [{"chat_id": "c1", "agent_id": "supervisor"}]
    # cancel targets the assistant turn, not a user message — it must not touch dedup.
    assert dedup.already_forwarded("m1") is False


@pytest.mark.asyncio
async def test_cancel_event_missing_agent_id_not_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = _FakeSupervisor(status="running")
    inbound = _InboundRecorder()
    await _run_consume(
        sse_body=_sse_bytes([("cancel", {"chat_id": "c1"})]),
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        monkeypatch=monkeypatch,
    )
    assert inbound.paths == []
    assert inbound.calls == []


@pytest.mark.asyncio
async def test_cancel_event_survives_gateway_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancel against an unreachable gateway is a logged drop, not a loop-killer."""
    supervisor = _FakeSupervisor(status="running")
    inbound = _InboundRecorder(behavior="connect_error")
    await _run_consume(
        sse_body=_sse_bytes([("cancel", {"chat_id": "c1", "agent_id": "supervisor"})]),
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        monkeypatch=monkeypatch,
    )
    assert inbound.paths == ["/api/channels/sellerclaw-ui/abort"]


@pytest.mark.asyncio
async def test_scheduled_run_forwarded_to_scheduled_run_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``scheduled_run`` frame is handed to the plugin's scheduled-run route (not inbound/chat)."""
    payload = {
        "run_id": "run-abc",
        "agent_id": "supervisor",
        "user_id": "u1",
        "instruction": "Summarize yesterday's orders and email me.",
        "session_key": "agent:supervisor:sellerclaw-ui:scheduled-task:run-abc",
    }
    supervisor = _FakeSupervisor(status="running")
    inbound = _InboundRecorder(behavior="ok")
    await _run_consume(
        sse_body=_sse_bytes([("scheduled_run", payload)]),
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        monkeypatch=monkeypatch,
    )
    assert inbound.paths == ["/api/channels/sellerclaw-ui/scheduled-run"]
    assert inbound.calls[0]["run_id"] == "run-abc"
    assert inbound.calls[0]["instruction"].startswith("Summarize")


@pytest.mark.asyncio
async def test_scheduled_run_not_forwarded_when_openclaw_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped agent never runs a scheduled task — the frame is dropped, not queued."""
    payload = {
        "run_id": "run-x",
        "agent_id": "supervisor",
        "user_id": "u1",
        "instruction": "do it",
    }
    supervisor = _FakeSupervisor(status="stopped")
    inbound = _InboundRecorder(behavior="ok")
    await _run_consume(
        sse_body=_sse_bytes([("scheduled_run", payload)]),
        supervisor=supervisor,
        inbound=inbound,
        agent_instance_id=uuid4(),
        monkeypatch=monkeypatch,
    )
    assert inbound.calls == []
