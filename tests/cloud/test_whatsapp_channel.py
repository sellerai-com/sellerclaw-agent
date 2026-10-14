from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from openclaw_diagnostics.gateway import GatewayError
from sellerclaw_agent.cloud.whatsapp_channel import (
    WhatsappChannelStatus,
    WhatsappStatusMonitor,
    parse_whatsapp_status,
    read_whatsapp_status,
)

from tests.cloud.conftest import FakeWhatsappGateway

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param(
            {"linked": True, "e164": "+15550001111", "connected": True},
            WhatsappChannelStatus(linked=True, connected=True, self_e164="+15550001111", logged_out=False, observed_at=_AT),
            id="linked-and-up",
        ),
        pytest.param(
            {"linked": False, "connected": False, "health_state": "stopped"},
            WhatsappChannelStatus(linked=False, connected=False, self_e164=None, logged_out=False, observed_at=_AT),
            id="not-linked-yet",
        ),
        pytest.param(
            {"linked": True, "e164": "+15550001111", "connected": False, "health_state": "logged-out"},
            WhatsappChannelStatus(linked=False, connected=False, self_e164="+15550001111", logged_out=True, observed_at=_AT),
            id="revoked-from-phone",
        ),
        pytest.param(
            {"linked": None, "connected": False, "health_state": None},
            WhatsappChannelStatus(linked=None, connected=False, self_e164=None, logged_out=False, observed_at=_AT),
            id="auth-unstable",
        ),
    ],
)
def test_parse_whatsapp_status(
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
    kwargs: dict[str, Any],
    expected: WhatsappChannelStatus,
) -> None:
    assert parse_whatsapp_status(make_whatsapp_status_payload(**kwargs), observed_at=_AT) == expected


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="none"),
        pytest.param({"channels": {"sellerclaw-ui": {"configured": True}}}, id="no-whatsapp"),
        pytest.param({"channels": "broken"}, id="malformed"),
    ],
)
def test_parse_whatsapp_status_without_a_summary(payload: Any) -> None:
    assert parse_whatsapp_status(payload, observed_at=_AT) is None


def test_ping_payload_shape() -> None:
    status = WhatsappChannelStatus(
        linked=True, connected=True, self_e164="+15550001111", logged_out=False, observed_at=_AT
    )
    assert status.to_ping_payload() == {
        "linked": True,
        "connected": True,
        "self_e164": "+15550001111",
        "logged_out": False,
        "observed_at": "2026-10-14T12:00:00+00:00",
    }


async def test_read_status_is_none_while_the_plugin_is_not_loaded(
    fake_whatsapp_gateway: FakeWhatsappGateway,
) -> None:
    fake_whatsapp_gateway.script(
        "channels.status",
        GatewayError("channels.status failed: INVALID_REQUEST: unknown channel: whatsapp"),
    )
    assert await read_whatsapp_status(fake_whatsapp_gateway.factory) is None
    assert fake_whatsapp_gateway.calls == [("channels.status", {"channel": "whatsapp"}, 3.0)]


async def test_read_status_raises_other_gateway_errors(fake_whatsapp_gateway: FakeWhatsappGateway) -> None:
    fake_whatsapp_gateway.script("channels.status", GatewayError("channels.status timed out after 3s"))
    with pytest.raises(GatewayError, match="timed out"):
        await read_whatsapp_status(fake_whatsapp_gateway.factory)


async def test_monitor_keeps_the_last_value_through_a_busy_gateway_and_drops_it_when_stopped(
    fake_whatsapp_gateway: FakeWhatsappGateway,
    make_whatsapp_status_payload: Callable[..., dict[str, Any]],
) -> None:
    monitor = WhatsappStatusMonitor(factory=fake_whatsapp_gateway.factory)
    assert monitor.latest_payload() is None

    fake_whatsapp_gateway.script(
        "channels.status",
        make_whatsapp_status_payload(linked=True, e164="+15550001111"),
        GatewayError("channels.status timed out after 3s"),
    )
    await monitor.refresh()
    first = monitor.latest_payload()
    assert first is not None and first["linked"] is True and first["self_e164"] == "+15550001111"

    await monitor.refresh()  # busy gateway: the stamped value stays
    assert monitor.latest_payload() == first

    fake_whatsapp_gateway.unreachable = True  # OpenClaw stopped
    await monitor.refresh()
    assert monitor.latest_payload() is None
