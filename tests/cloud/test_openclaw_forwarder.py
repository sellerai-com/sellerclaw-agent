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
            http_client=shared,
        )
        await fwd.post_inbound_json({"chat_id": "c1", "agent_id": "a", "user_id": "u1", "text": "x"})
        await fwd.post_inbound_json({"chat_id": "c2", "agent_id": "a", "user_id": "u1", "text": "y"})
    assert len(calls) == 2
    assert all(c.endswith("/channels/sellerclaw-ui/inbound") for c in calls)


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
        transport=transport,
    )
    await fwd.post_inbound_json(
        {"chat_id": "c1", "agent_id": "supervisor", "user_id": "u1", "text": "hi"},
    )
    assert captured["url"].endswith("/channels/sellerclaw-ui/inbound")
    assert captured["auth"] == "Bearer hooks-secret"
    assert json.loads(captured["body"])["text"] == "hi"


def test_openclaw_gateway_base_url_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_GATEWAY_HTTP_BASE", raising=False)
    monkeypatch.setenv("OPENCLAW_PORT_GATEWAY", "8899")
    assert openclaw_gateway_base_url() == "http://127.0.0.1:8899"
    monkeypatch.setenv("OPENCLAW_GATEWAY_HTTP_BASE", "http://custom:7777")
    assert openclaw_gateway_base_url() == "http://custom:7777"
