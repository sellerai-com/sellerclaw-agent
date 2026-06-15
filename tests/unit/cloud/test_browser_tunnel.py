from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sellerclaw_agent.cloud import browser_tunnel as bt
from sellerclaw_agent.cloud.browser_tunnel import (
    BrowserViewTunnelManager,
    run_browser_view_tunnel,
)

pytestmark = pytest.mark.unit


class _FakeClient:
    def __init__(self, *, session_id: UUID | None, token: str = "sca_test") -> None:
        self._session_id = session_id
        self._token = token
        self.session_fetches = 0

    async def fetch_browser_view_session_id(self) -> UUID | None:
        self.session_fetches += 1
        return self._session_id

    def bearer_token(self) -> str:
        return self._token


async def test_tunnel_stops_when_session_gone() -> None:
    client = _FakeClient(session_id=None)

    await asyncio.wait_for(
        run_browser_view_tunnel(session_id=uuid4(), client=client),  # type: ignore[arg-type]
        timeout=2.0,
    )

    assert client.session_fetches == 1


async def test_tunnel_stops_when_session_replaced() -> None:
    client = _FakeClient(session_id=uuid4())

    await asyncio.wait_for(
        run_browser_view_tunnel(session_id=uuid4(), client=client),  # type: ignore[arg-type]
        timeout=2.0,
    )

    assert client.session_fetches == 1


async def test_tunnel_stops_on_session_ended_close(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    client = _FakeClient(session_id=session_id)
    serve_calls = 0

    async def _fake_serve(*, session_id: UUID, bearer_token: str) -> None:
        nonlocal serve_calls
        serve_calls += 1
        raise bt._SessionEnded

    monkeypatch.setattr(bt, "_serve_one_tunnel", _fake_serve)

    await asyncio.wait_for(
        run_browser_view_tunnel(session_id=session_id, client=client),  # type: ignore[arg-type]
        timeout=2.0,
    )

    assert serve_calls == 1


async def test_tunnel_redials_after_clean_detach(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    client = _FakeClient(session_id=session_id)
    serve_calls = 0

    async def _fake_serve(*, session_id: UUID, bearer_token: str) -> None:
        nonlocal serve_calls
        serve_calls += 1
        if serve_calls >= 2:
            raise bt._SessionEnded  # end the test after proving the redial

    monkeypatch.setattr(bt, "_serve_one_tunnel", _fake_serve)

    await asyncio.wait_for(
        run_browser_view_tunnel(session_id=session_id, client=client),  # type: ignore[arg-type]
        timeout=5.0,
    )

    assert serve_calls == 2
    assert client.session_fetches == 2  # session re-validated before each dial


async def test_manager_start_is_idempotent_for_same_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    client = _FakeClient(session_id=session_id)
    started = 0

    async def _fake_run(*, session_id: UUID, client: object) -> None:
        nonlocal started
        started += 1
        await asyncio.sleep(3600)

    monkeypatch.setattr(bt, "run_browser_view_tunnel", _fake_run)
    manager = BrowserViewTunnelManager()

    manager.start(session_id=session_id, client=client)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    manager.start(session_id=session_id, client=client)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert started == 1
    assert manager.running_session_id == session_id
    manager.stop()
    await asyncio.sleep(0)
    assert manager.running_session_id is None


async def test_manager_replaces_tunnel_for_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(session_id=uuid4())
    sessions_started: list[UUID] = []

    async def _fake_run(*, session_id: UUID, client: object) -> None:
        sessions_started.append(session_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(bt, "run_browser_view_tunnel", _fake_run)
    manager = BrowserViewTunnelManager()
    first, second = uuid4(), uuid4()

    manager.start(session_id=first, client=client)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    manager.start(session_id=second, client=client)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert sessions_started == [first, second]
    assert manager.running_session_id == second
    manager.stop()


def test_cloud_tunnel_url_uses_ws_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bt, "get_sellerclaw_api_url", lambda: "https://api.example.com")
    session_id = uuid4()

    url = bt._cloud_tunnel_url(session_id)

    assert url == f"wss://api.example.com/agent/browser-view/tunnel?session_id={session_id}"


def test_local_vnc_headers_use_env_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VNC_PASSWORD", "s3cret")

    headers = bt._local_vnc_headers()

    assert headers["Authorization"] == "Basic dXNlcjpzM2NyZXQ="  # base64(user:s3cret)
