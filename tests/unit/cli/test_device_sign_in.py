"""``sellerclaw-agent login --browser``: the sign-in code survives the network blinking.

The CLI asks the local agent for a code (``/auth/device/start``) and then polls it
(``/auth/device/poll``) until the owner confirms in the browser — up to 15 minutes. The local agent
answers 502 whenever its own request to the cloud failed; that is a blip, not the end of the sign-in:
the code stays valid in the cloud, and a confirmation made meanwhile lands on the next poll.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from rich.console import Console
from sellerclaw_agent import cli

pytestmark = pytest.mark.unit

_START_OK = httpx.Response(
    200,
    json={
        "verification_uri": "https://app.example/auth/device?code=ABCD-EFGH",
        "user_code": "ABCD-EFGH",
        "device_code": "dev-code",
        "interval": 5,
        "expires_in": 900,
    },
)
_PENDING = httpx.Response(200, json={"status": "pending", "auth": None})
_COMPLETED = httpx.Response(200, json={"status": "completed", "auth": {"connected": True}})
_CLOUD_DOWN = httpx.Response(502, json={"detail": ""})
_TIMEOUT = "timeout"


@pytest.fixture(autouse=True)
def _local_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "cli-test-local-key")
    cli._clear_local_control_plane_key_cache()
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    yield
    cli._clear_local_control_plane_key_cache()


class _LocalAgent:
    """The local agent's sign-in routes: answers in order per route, the last one repeats."""

    def __init__(self, *, start: list[Any], poll: list[Any]) -> None:
        self.answers = {"/auth/device/start": start, "/auth/device/poll": poll}
        self.calls = {"/auth/device/start": 0, "/auth/device/poll": 0}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        answers = self.answers[path]
        answer = answers[min(self.calls[path], len(answers) - 1)]
        self.calls[path] += 1
        if answer == _TIMEOUT:
            raise httpx.ReadTimeout("timed out", request=request)
        return answer


@pytest.fixture()
def local_agent(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _LocalAgent]:
    def _install(*, start: list[Any], poll: list[Any]) -> _LocalAgent:
        agent = _LocalAgent(start=start, poll=poll)
        monkeypatch.setattr(
            cli,
            "sync_client",
            lambda **kw: httpx.Client(transport=httpx.MockTransport(agent), timeout=kw.get("timeout")),
        )
        return agent

    return _install


def _console() -> Console:
    return Console(record=True, width=200, theme=cli._THEME)


@pytest.mark.parametrize(
    "poll",
    [
        pytest.param([_PENDING, _COMPLETED], id="steady-network"),
        pytest.param([_CLOUD_DOWN, _TIMEOUT, _CLOUD_DOWN, _PENDING, _COMPLETED], id="network-blinks-meanwhile"),
        pytest.param([_CLOUD_DOWN] * 6 + [_COMPLETED], id="cloud-away-for-a-while"),
    ],
)
def test_the_wait_for_the_confirmation_outlasts_network_blips(local_agent, poll: list[Any]) -> None:
    agent = local_agent(start=[_START_OK], poll=poll)
    console = _console()

    cli._device_flow(console, "http://agent", open_browser=False)

    assert agent.calls["/auth/device/poll"] == len(poll)
    out = console.export_text()
    assert "Signed in successfully!" in out
    assert "went wrong" not in out


@pytest.mark.parametrize(
    ("poll", "message"),
    [
        pytest.param(
            [httpx.Response(400, json={"detail": "Device authorization failed: authorization_invalid"})],
            "Sign-in failed: Device authorization failed: authorization_invalid",
            id="code-refused",
        ),
        pytest.param([httpx.Response(400, json={"detail": ""})], "Sign-in failed: HTTP 400", id="refused-without-reason"),
        pytest.param([httpx.Response(401, json={"detail": "Invalid local API key"})], "Sign-in failed: Invalid local API key", id="local-key"),
    ],
)
def test_a_refused_code_ends_the_sign_in_with_the_reason(local_agent, poll: list[Any], message: str) -> None:
    agent = local_agent(start=[_START_OK], poll=[_CLOUD_DOWN, *poll])
    console = _console()

    with pytest.raises(SystemExit) as exc:
        cli._device_flow(console, "http://agent", open_browser=False)

    assert exc.value.code == 1
    assert agent.calls["/auth/device/poll"] == 2
    assert message in console.export_text()


def test_a_cloud_that_never_comes_back_ends_when_the_code_expires(
    monkeypatch: pytest.MonkeyPatch, local_agent
) -> None:
    clock = {"now": 0.0}

    def _monotonic() -> float:
        clock["now"] += 100.0  # every poll costs 100 s of the code's 900
        return clock["now"]

    monkeypatch.setattr(cli.time, "monotonic", _monotonic)
    agent = local_agent(start=[_START_OK], poll=[_CLOUD_DOWN])
    console = _console()

    with pytest.raises(SystemExit) as exc:
        cli._device_flow(console, "http://agent", open_browser=False)

    assert exc.value.code == 1
    assert 3 <= agent.calls["/auth/device/poll"] <= 9, "kept polling until the code ran out"
    assert "Sign-in timed out." in console.export_text()


@pytest.mark.parametrize(
    ("start", "starts"),
    [
        pytest.param([_START_OK], 1, id="first-try"),
        pytest.param([_CLOUD_DOWN, httpx.Response(504), _START_OK], 3, id="after-two-blips"),
    ],
)
def test_getting_a_code_retries_a_blip(local_agent, start: list[Any], starts: int) -> None:
    agent = local_agent(start=start, poll=[_COMPLETED])
    console = _console()

    cli._device_flow(console, "http://agent", open_browser=False)

    assert agent.calls["/auth/device/start"] == starts
    assert "ABCD-EFGH" in console.export_text()


def test_getting_a_code_gives_up_after_its_tries(local_agent) -> None:
    agent = local_agent(start=[_CLOUD_DOWN], poll=[_COMPLETED])
    console = _console()

    with pytest.raises(SystemExit) as exc:
        cli._device_flow(console, "http://agent", open_browser=False)

    assert exc.value.code == 1
    assert agent.calls["/auth/device/start"] == cli.DEVICE_START_ATTEMPTS
    assert agent.calls["/auth/device/poll"] == 0
    assert "We can't reach the SellerClaw cloud right now." in console.export_text()
