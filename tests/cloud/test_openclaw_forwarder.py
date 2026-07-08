from __future__ import annotations

import json

import httpx
import pytest
from sellerclaw_agent.cloud.openclaw_forwarder import LocalOpenClawForwarder, openclaw_gateway_base_url

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_local_forwarder_uses_shared_http_client() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as shared:
        fwd = LocalOpenClawForwarder(
            base_url="http://gw.test",
            hooks_token="hooks-secret",
            gateway_token="gw-secret",
            http_client=shared,
        )
        await fwd.post_inbound_json({"chat_id": "c1", "agent_id": "a", "user_id": "u1", "text": "x"})
        await fwd.post_inbound_json({"chat_id": "c2", "agent_id": "a", "user_id": "u1", "text": "y"})
    assert len(calls) == 2
    assert all(c.endswith("/api/channels/sellerclaw-ui/inbound") for c in calls)


@pytest.mark.asyncio
async def test_local_forwarder_posts_inbound() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=transport,
    )
    await fwd.post_inbound_json(
        {"chat_id": "c1", "agent_id": "supervisor", "user_id": "u1", "text": "hi"},
    )
    # Inbound goes through OpenClaw's gateway-authenticated plugin route: the
    # /api/channels prefix is what makes the gateway grant operator scopes to the
    # agent run (sessions_spawn needs operator.write), and auth is the gateway token.
    assert captured["url"].endswith("/api/channels/sellerclaw-ui/inbound")
    assert captured["auth"] == "Bearer gw-secret"
    assert json.loads(captured["body"])["text"] == "hi"


@pytest.mark.asyncio
async def test_local_forwarder_posts_abort() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=transport,
    )
    await fwd.post_abort_json({"chat_id": "c1", "agent_id": "supervisor"})
    assert captured["url"].endswith("/api/channels/sellerclaw-ui/abort")
    assert captured["auth"] == "Bearer gw-secret"
    assert json.loads(captured["body"]) == {"chat_id": "c1", "agent_id": "supervisor"}


@pytest.mark.asyncio
async def test_local_forwarder_posts_hooks_agent_with_hooks_token() -> None:
    """/hooks/agent is authenticated by OpenClaw's hooks token, not the gateway token."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=transport,
    )
    await fwd.post_hooks_agent_json({"event": "ping"})
    assert captured["url"].endswith("/hooks/agent")
    assert captured["auth"] == "Bearer hooks-secret"


@pytest.mark.asyncio
async def test_local_forwarder_posts_scheduled_run_with_gateway_token() -> None:
    """A scheduled run goes through the gateway-authed plugin route like inbound."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=transport,
    )
    await fwd.post_scheduled_run_json(
        {"run_id": "run-1", "agent_id": "supervisor", "user_id": "u1", "instruction": "do it"},
    )
    assert captured["url"].endswith("/api/channels/sellerclaw-ui/scheduled-run")
    assert captured["auth"] == "Bearer gw-secret"
    assert json.loads(captured["body"])["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_local_forwarder_scheduled_run_raises_on_non_2xx() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=transport,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await fwd.post_scheduled_run_json({"run_id": "run-1", "instruction": "x"})


@pytest.mark.asyncio
async def test_local_forwarder_abort_raises_on_non_2xx() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=transport,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await fwd.post_abort_json({"chat_id": "c1", "agent_id": "supervisor"})


def test_openclaw_gateway_base_url_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_GATEWAY_HTTP_BASE", raising=False)
    monkeypatch.setenv("OPENCLAW_PORT_GATEWAY", "8899")
    assert openclaw_gateway_base_url() == "http://127.0.0.1:8899"
    monkeypatch.setenv("OPENCLAW_GATEWAY_HTTP_BASE", "http://custom:7777")
    assert openclaw_gateway_base_url() == "http://custom:7777"
