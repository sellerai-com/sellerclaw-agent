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
async def test_local_forwarder_posts_cron_command_and_returns_the_outcome() -> None:
    """A cron command goes through the gateway-authed plugin route and its JSON answer comes back."""
    captured: dict[str, str] = {}
    outcome = {"ok": True, "error": None, "store_epoch": "e1", "job": {"id": "j1"}, "run_id": None}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json=outcome)

    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=httpx.MockTransport(handler),
    )
    frame = {"request_id": "r1", "action": "add", "job_id": None, "job": {"name": "x"}, "patch": None}
    assert await fwd.post_cron_command_json(frame) == outcome
    assert captured["url"] == "http://gw.test/api/channels/sellerclaw-ui/cron/command"
    assert captured["auth"] == "Bearer gw-secret"
    assert json.loads(captured["body"]) == frame


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        pytest.param(httpx.Response(400, json={"error": "bad"}), httpx.HTTPStatusError, id="refused"),
        pytest.param(httpx.Response(200, json=["not", "an", "object"]), ValueError, id="not-an-object"),
    ],
)
async def test_local_forwarder_cron_command_raises_on_a_bad_answer(
    response: httpx.Response, error: type[Exception]
) -> None:
    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=httpx.MockTransport(lambda _request: response),
    )
    with pytest.raises(error):
        await fwd.post_cron_command_json({"request_id": "r1", "action": "run", "job_id": "j1"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        pytest.param(200, {"ok": True, "standby": True, "job_ids": ["a"]}, True, id="held"),
        pytest.param(503, {"ok": False, "error": "scheduler not ready"}, False, id="not-yet"),
        pytest.param(404, None, False, id="no-route"),
    ],
)
async def test_local_forwarder_cron_standby(status: int, body: dict | None, expected: bool) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    fwd = LocalOpenClawForwarder(
        base_url="http://gw.test",
        hooks_token="hooks-secret",
        gateway_token="gw-secret",
        transport=httpx.MockTransport(handler),
    )
    held, payload = await fwd.post_cron_standby_json(standby=True)
    assert held is expected
    assert payload == (body or {})
    assert seen == {"path": "/api/channels/sellerclaw-ui/cron/standby", "body": {"standby": True}}


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
    monkeypatch.setenv("OPENCLAW_PORT_GATEWAY_LOCAL", "8899")
    assert openclaw_gateway_base_url() == "http://127.0.0.1:8899"
    monkeypatch.setenv("OPENCLAW_GATEWAY_HTTP_BASE", "http://custom:7777")
    assert openclaw_gateway_base_url() == "http://custom:7777"


def test_openclaw_gateway_base_url_never_falls_back_to_the_nginx_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The published nginx port answers in-container callers with 403, so it is never a default.

    A request that reaches the gateway through nginx from loopback carries a forwarded client
    address that is itself loopback -- no client to attribute -- and OpenClaw refuses it with
    ``proxy_attribution_required``. Falling back to that port made the readiness probe read a
    healthy agent as "still starting" and pinned the owner on the launch screen.
    """
    monkeypatch.delenv("OPENCLAW_GATEWAY_HTTP_BASE", raising=False)
    monkeypatch.delenv("OPENCLAW_PORT_GATEWAY_LOCAL", raising=False)
    monkeypatch.setenv("OPENCLAW_PORT_GATEWAY", "7788")
    assert openclaw_gateway_base_url() == "http://127.0.0.1:7789"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param("post_inbound_json", "/api/channels/sellerclaw-ui/inbound", id="inbound"),
        pytest.param("post_abort_json", "/api/channels/sellerclaw-ui/abort", id="abort"),
        pytest.param("post_cron_command_json", "/api/channels/sellerclaw-ui/cron/command", id="cron-command"),
        pytest.param("post_hooks_agent_json", "/hooks/agent", id="hooks"),
    ],
)
async def test_local_forwarder_never_reuses_a_gateway_connection(method: str, path: str) -> None:
    """The gateway admits one request per connection at a time and holds a chat handler for its
    whole turn, so a message reusing that connection would wait for the turn to end. Every
    request asks for a connection of its own."""
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, request.headers.get("connection", "")))
        return httpx.Response(202, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as shared:
        fwd = LocalOpenClawForwarder(
            base_url="http://gw.test",
            hooks_token="hooks-secret",
            gateway_token="gw-secret",
            http_client=shared,
        )
        await getattr(fwd, method)({"chat_id": "c1", "agent_id": "supervisor"})

    assert captured == [(path, "close")]
