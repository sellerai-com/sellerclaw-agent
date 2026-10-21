"""Sending a ready text to the owner's messenger through the gateway, as written.

The cloud's important notifications ("1 new order", "credits ran out") used to reach a messenger
as a ``/hooks/agent`` hook, and none ever arrived: the gateway never sends a hook's text, it runs
an agent turn on it, and the agent ends such a turn without a reply. The gateway's ``send`` RPC is
the plain send — what ``openclaw message send`` uses — so the text goes out unchanged and no model
runs (and nothing is charged).

``send`` also writes the text into the agent's conversation with that chat as its own reply, so
when the owner answers "which order?" the agent knows what it said. For WhatsApp this is the only
way in at all: the linked session lives inside the gateway.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Final

from openclaw_diagnostics.gateway import GatewayConnection

from sellerclaw_agent.cloud.whatsapp_channel import GatewayFactory, WhatsappGatewayConnection

#: ``send`` is an operator.write method; nothing more is asked for.
SEND_SCOPES: Final[tuple[str, ...]] = ("operator.write",)
#: The gateway is single-threaded and stalls during heavy tool calls; a send waits that out.
SEND_CALL_TIMEOUT_S: Final[float] = 30.0
#: What the cloud may set on a send. Everything else the RPC accepts (media, replies, threads) is
#: not ours to pass through unvetted.
_REQUIRED: Final[tuple[str, ...]] = ("channel", "to", "message", "idempotencyKey")
_OPTIONAL: Final[tuple[str, ...]] = ("agentId",)


def default_send_gateway_factory() -> AbstractAsyncContextManager[WhatsappGatewayConnection]:
    return GatewayConnection(scopes=SEND_SCOPES)


def send_params(body: dict[str, Any]) -> dict[str, Any]:
    """The RPC params for a cloud send body; ``ValueError`` when a required field is missing."""
    missing = [key for key in _REQUIRED if not isinstance(body.get(key), str) or not body[key].strip()]
    if missing:
        raise ValueError(f"send body is missing {', '.join(missing)}")
    return {key: body[key] for key in (*_REQUIRED, *_OPTIONAL) if isinstance(body.get(key), str)}


async def send_text(
    body: dict[str, Any], *, gateway_factory: GatewayFactory = default_send_gateway_factory
) -> None:
    """Send the text; a refusal (WhatsApp not linked, reconnecting, the number not allowed) raises
    ``GatewayError`` with the gateway's own words.

    ``idempotencyKey`` is the cloud's hook id: a send repeated with it inside a few minutes is
    answered from the gateway's cache instead of going out twice.
    """
    params = send_params(body)
    async with gateway_factory() as gateway:
        await gateway.call("send", params, timeout=SEND_CALL_TIMEOUT_S)
