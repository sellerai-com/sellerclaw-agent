from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from openclaw_diagnostics.gateway import GatewayError
from sellerclaw_agent.cloud.whatsapp_link import (
    LinkStatus,
    WhatsappLinkManager,
    WhatsappLinkRunner,
)

from tests.cloud.conftest import FakeWhatsappGateway, FakeWhatsappLinkCloud

pytestmark = pytest.mark.unit

_LOGIN_ID = UUID("33333333-3333-4333-8333-333333333333")
_QR_1 = "data:image/png;base64,AAAA"
_QR_2 = "data:image/png;base64,BBBB"
_UNKNOWN_CHANNEL = GatewayError("channels.status failed: INVALID_REQUEST: unknown channel: whatsapp")


class _Clock:
    """Fake monotonic clock; every sleep advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _runner(
    gateway: FakeWhatsappGateway,
    cloud: FakeWhatsappLinkCloud,
    *,
    force: bool = False,
    clock: _Clock | None = None,
    on_linked: Callable[[], None] | None = None,
) -> WhatsappLinkRunner:
    tick = clock or _Clock()
    return WhatsappLinkRunner(
        login_id=_LOGIN_ID,
        force=force,
        cloud=cloud,
        gateway_factory=gateway.factory,
        on_linked=on_linked,
        sleep=tick.sleep,
        clock=tick,
    )


async def test_link_shows_each_qr_then_reports_the_linked_number(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
) -> None:
    gateway = fake_whatsapp_gateway
    gateway.script(
        "channels.status",
        make_whatsapp_status_payload(linked=False, connected=False),
        make_whatsapp_status_payload(linked=True, e164="+15550001111"),
    )
    gateway.script("web.login.start", {"qrDataUrl": _QR_1, "message": "Scan this QR"})
    gateway.script(
        "web.login.wait",
        {"connected": False, "message": "Still waiting for the QR scan. Let me know when you've scanned it."},
        {"connected": False, "qrDataUrl": _QR_2, "message": "QR refreshed."},
        {"connected": True, "message": "✅ Linked! WhatsApp is ready."},
    )
    linked: list[bool] = []

    status = await _runner(gateway, fake_whatsapp_link_cloud, on_linked=lambda: linked.append(True)).run()

    assert status is LinkStatus.CONNECTED
    assert fake_whatsapp_link_cloud.states == [
        {"status": "preparing", "qr_data_url": None, "reason": None, "message": None, "self_e164": None},
        {"status": "qr", "qr_data_url": _QR_1, "reason": None, "message": None, "self_e164": None},
        {"status": "qr", "qr_data_url": _QR_2, "reason": None, "message": None, "self_e164": None},
        {"status": "connected", "qr_data_url": None, "reason": None, "message": None, "self_e164": "+15550001111"},
    ]
    assert linked == [True]
    start_params = next(params for method, params, _ in gateway.calls if method == "web.login.start")
    assert start_params == {"channel": "whatsapp", "force": False, "timeoutMs": 30_000}
    wait_params = [params for method, params, _ in gateway.calls if method == "web.login.wait"]
    # Each wait carries the QR on screen, so the plugin returns as soon as it rotates.
    assert [params["currentQrDataUrl"] for params in wait_params] == [_QR_1, _QR_1, _QR_2]
    assert all(timeout == 30.0 for method, _, timeout in gateway.calls if method == "web.login.wait")


async def test_link_waits_for_the_plugin_to_load(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
) -> None:
    """The channel appears only once the manifest is applied and OpenClaw reloads it."""
    gateway = fake_whatsapp_gateway
    gateway.script(
        "channels.status",
        _UNKNOWN_CHANNEL,
        GatewayError("channels.status timed out after 3s"),
        make_whatsapp_status_payload(linked=False, connected=False),
    )
    gateway.script("web.login.start", {"qrDataUrl": _QR_1})
    gateway.script("web.login.wait", {"connected": False, "message": "The login QR expired. Ask me to generate a new one."})

    status = await _runner(gateway, fake_whatsapp_link_cloud).run()

    assert status is LinkStatus.EXPIRED
    assert fake_whatsapp_link_cloud.statuses() == ["preparing", "qr", "expired"]
    assert gateway.methods_called()[:4] == [
        "channels.status",
        "channels.status",
        "channels.status",
        "web.login.start",
    ]


async def test_link_fails_when_the_plugin_never_loads(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
) -> None:
    fake_whatsapp_gateway.script("channels.status", _UNKNOWN_CHANNEL)

    status = await _runner(fake_whatsapp_gateway, fake_whatsapp_link_cloud).run()

    assert status is LinkStatus.FAILED
    assert fake_whatsapp_link_cloud.states[-1]["status"] == "failed"
    assert fake_whatsapp_link_cloud.states[-1]["reason"] == "channel_not_ready"
    assert "web.login.start" not in fake_whatsapp_gateway.methods_called()


async def test_link_fails_when_openclaw_is_not_running(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
) -> None:
    fake_whatsapp_gateway.unreachable = True

    status = await _runner(fake_whatsapp_gateway, fake_whatsapp_link_cloud).run()

    assert status is LinkStatus.FAILED
    assert fake_whatsapp_link_cloud.statuses() == ["preparing", "failed"]
    assert fake_whatsapp_link_cloud.states[-1]["reason"] == "assistant_not_running"


@pytest.mark.parametrize(
    "start_answer",
    [
        pytest.param({"message": "WhatsApp is already linked (+15550001111). Say “relink” if you want a fresh QR."}, id="already-linked"),
        pytest.param({"message": "WhatsApp recovered the existing linked session (+15550001111).", "connected": True}, id="recovered"),
    ],
)
async def test_link_reports_connected_without_a_qr_when_already_linked(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
    start_answer: dict[str, Any],
) -> None:
    fake_whatsapp_gateway.script("channels.status", make_whatsapp_status_payload(linked=True))
    fake_whatsapp_gateway.script("web.login.start", start_answer)

    status = await _runner(fake_whatsapp_gateway, fake_whatsapp_link_cloud).run()

    assert status is LinkStatus.CONNECTED
    assert fake_whatsapp_link_cloud.statuses() == ["preparing", "connected"]
    assert fake_whatsapp_link_cloud.states[-1]["self_e164"] == "+15550001111"
    assert "web.login.wait" not in fake_whatsapp_gateway.methods_called()


async def test_link_another_phone_passes_force(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
) -> None:
    fake_whatsapp_gateway.script("channels.status", make_whatsapp_status_payload())
    fake_whatsapp_gateway.script("web.login.start", {"message": "WhatsApp login failed: existing auth could not be cleared."})

    status = await _runner(fake_whatsapp_gateway, fake_whatsapp_link_cloud, force=True).run()

    assert status is LinkStatus.FAILED
    start_params = next(p for m, p, _ in fake_whatsapp_gateway.calls if m == "web.login.start")
    assert start_params["force"] is True
    assert fake_whatsapp_link_cloud.states[-1]["reason"] == "login_failed"
    assert fake_whatsapp_link_cloud.states[-1]["message"] == "WhatsApp login failed: existing auth could not be cleared."


async def test_link_retries_while_auth_state_settles(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
) -> None:
    fake_whatsapp_gateway.script("channels.status", make_whatsapp_status_payload(linked=False))
    fake_whatsapp_gateway.script(
        "web.login.start",
        {"code": "WHATSAPP_AUTH_UNSTABLE", "message": "WhatsApp auth state is still stabilizing."},
        {"qrDataUrl": _QR_1},
    )
    fake_whatsapp_gateway.script("web.login.wait", {"connected": False, "message": "Login ended without a connection."})

    status = await _runner(fake_whatsapp_gateway, fake_whatsapp_link_cloud).run()

    assert status is LinkStatus.FAILED
    assert fake_whatsapp_gateway.methods_called().count("web.login.start") == 2
    assert fake_whatsapp_link_cloud.statuses() == ["preparing", "qr", "failed"]


@pytest.mark.parametrize(
    ("wait_answer", "expected_status", "expected_reason"),
    [
        pytest.param(
            {"connected": False, "message": "The login QR expired. Ask me to generate a new one."},
            "expired",
            None,
            id="expired",
        ),
        pytest.param(
            {"connected": False, "message": "No active WhatsApp login in progress."},
            "failed",
            "replaced",
            id="no-active-login",
        ),
        pytest.param(
            {"connected": False, "message": "WhatsApp login failed: Connection Failure"},
            "failed",
            "login_failed",
            id="login-failed",
        ),
        pytest.param(GatewayError("web.login.wait failed: UNAVAILABLE: boom"), "failed", "login_failed", id="rpc-error"),
    ],
)
async def test_link_terminal_wait_outcomes(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
    wait_answer: Any,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    fake_whatsapp_gateway.script("channels.status", make_whatsapp_status_payload(linked=False))
    fake_whatsapp_gateway.script("web.login.start", {"qrDataUrl": _QR_1})
    fake_whatsapp_gateway.script("web.login.wait", wait_answer)

    await _runner(fake_whatsapp_gateway, fake_whatsapp_link_cloud).run()

    last = fake_whatsapp_link_cloud.states[-1]
    assert last["status"] == expected_status
    assert last["reason"] == expected_reason


async def test_link_expires_after_the_login_deadline(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
) -> None:
    clock = _Clock()
    fake_whatsapp_gateway.script("channels.status", make_whatsapp_status_payload(linked=False))
    fake_whatsapp_gateway.script("web.login.start", {"qrDataUrl": _QR_1})
    fake_whatsapp_gateway.script("web.login.wait", {"connected": False, "message": "Still waiting for the QR scan."})

    def _slow_factory() -> Any:
        # Every gateway round-trip burns 20 s, as the real long-poll does.
        clock.now += 20.0
        return fake_whatsapp_gateway.factory()

    runner = _runner(fake_whatsapp_gateway, fake_whatsapp_link_cloud, clock=clock)
    runner.gateway_factory = _slow_factory

    status = await runner.run()

    assert status is LinkStatus.EXPIRED
    assert fake_whatsapp_link_cloud.statuses() == ["preparing", "qr", "expired"]
    # 180 s budget / 20 s per wait.
    assert fake_whatsapp_gateway.methods_called().count("web.login.wait") == 9


async def test_link_keeps_waiting_through_a_busy_gateway(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
) -> None:
    """A wait that times out while a long tool call holds the gateway is not a failed login."""
    fake_whatsapp_gateway.script(
        "channels.status",
        make_whatsapp_status_payload(linked=False),
        make_whatsapp_status_payload(linked=True),
    )
    fake_whatsapp_gateway.script("web.login.start", {"qrDataUrl": _QR_1})
    fake_whatsapp_gateway.script(
        "web.login.wait",
        GatewayError("web.login.wait timed out after 30s"),
        {"connected": True},
    )

    status = await _runner(fake_whatsapp_gateway, fake_whatsapp_link_cloud).run()

    assert status is LinkStatus.CONNECTED
    assert fake_whatsapp_link_cloud.statuses() == ["preparing", "qr", "connected"]


async def test_link_resends_the_outcome_until_the_cloud_takes_it(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
) -> None:
    """Nothing follows the final state to replace it: a lost "connected" would leave a used code
    on the website until the attempt expires."""
    cloud = FakeWhatsappLinkCloud(fail_statuses={"connected": 2})
    fake_whatsapp_gateway.script(
        "channels.status", make_whatsapp_status_payload(linked=True)
    )
    fake_whatsapp_gateway.script("web.login.start", {"connected": True})

    status = await _runner(fake_whatsapp_gateway, cloud).run()

    assert status is LinkStatus.CONNECTED
    assert cloud.statuses() == ["preparing", "connected"]
    assert cloud.failed == ["connected", "connected"]


async def test_link_stops_when_the_cloud_closed_the_attempt(
    fake_whatsapp_gateway: FakeWhatsappGateway,
) -> None:
    cloud = FakeWhatsappLinkCloud(accept=False)

    status = await _runner(fake_whatsapp_gateway, cloud).run()

    assert status is LinkStatus.FAILED
    assert cloud.statuses() == ["preparing"]
    assert fake_whatsapp_gateway.calls == []


async def test_manager_keeps_one_runner_per_attempt(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    fake_whatsapp_link_cloud: FakeWhatsappLinkCloud,
) -> None:
    fake_whatsapp_gateway.script("channels.status", _UNKNOWN_CHANNEL)
    blocker = asyncio.Event()

    async def _block(_seconds: float) -> None:
        await blocker.wait()

    def _make(login_id: UUID) -> WhatsappLinkRunner:
        return WhatsappLinkRunner(
            login_id=login_id,
            force=False,
            cloud=fake_whatsapp_link_cloud,
            gateway_factory=fake_whatsapp_gateway.factory,
            sleep=_block,
        )

    manager = WhatsappLinkManager()
    manager.start(_make(_LOGIN_ID))
    await asyncio.sleep(0)
    first_task = manager._task
    manager.start(_make(_LOGIN_ID))  # the same command delivered again
    assert manager._task is first_task

    other = UUID("44444444-4444-4444-8444-444444444444")
    manager.start(_make(other))
    await asyncio.sleep(0)
    assert first_task is not None and first_task.cancelled()
    assert manager.running_login_id == other

    manager.stop()
    assert manager.running_login_id is None
