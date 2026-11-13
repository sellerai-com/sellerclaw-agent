"""Local routes behind ``sellerclaw-agent connect|disconnect`` and the plugin's limit nudge.

The CLI does the owner-facing part in the terminal (installing Claude Code, its own sign-in); these
routes record the result and put the agent on — or off — the owner's plan by rebuilding the
OpenClaw config from the saved manifest. Owner routes need the local API key; the plugin's nudge
needs the hooks token, like the other plugin callbacks.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sellerclaw_agent.bundle.manifest import bundle_manifest_from_mapping
from sellerclaw_agent.cloud.supervisor_manager import SupervisorContainerManager, create_supervisor_manager
from sellerclaw_agent.server.deps import require_local_api_key
from sellerclaw_agent.server.media_upload import require_hooks_token
from sellerclaw_agent.server.storage import ManifestStorage
from sellerclaw_agent.subscription import chatgpt_auth, claude_cli
from sellerclaw_agent.subscription.monitor import get_ai_subscription_monitor
from sellerclaw_agent.subscription.state import clear_choice, load_choice, save_choice

_log = structlog.get_logger(__name__)


def _data_dir() -> Path:
    from sellerclaw_agent.server.app import _get_data_dir

    return _get_data_dir()


def _manager() -> SupervisorContainerManager:
    return create_supervisor_manager()


owner_router = APIRouter(prefix="/ai-subscription", dependencies=[Depends(require_local_api_key)])
plugin_router = APIRouter(prefix="/internal/ai-subscription", dependencies=[Depends(require_hooks_token)])


class AiSubscriptionStateResponse(BaseModel):
    provider: Literal["chatgpt", "claude"] | None
    status: dict[str, Any] | None = None
    #: Whether the saved manifest carries this provider's lineup; without it the agent stays on
    #: credits even when connected (an agent hosted in our cloud, or the feature switched off).
    #: ``None`` while the cloud has not sent its configuration yet (right after sign-in): unknown.
    offered: bool | None = False
    applied: bool = False
    error: str | None = None


def _lineup_offered(data_dir: Path, provider: str) -> bool | None:
    try:
        mapping = ManifestStorage(data_dir).load()
        if mapping is None:
            return None
        return provider in bundle_manifest_from_mapping(mapping).llm.subscription
    except (OSError, TypeError, ValueError):
        return False


def _reapply(data_dir: Path, manager: SupervisorContainerManager) -> tuple[bool, str | None]:
    """Rebuild the OpenClaw config from the saved manifest so the change takes effect now."""
    try:
        mapping = ManifestStorage(data_dir).load()
    except (OSError, ValueError) as exc:
        return False, str(exc)[:300]
    if mapping is None:
        # Not signed in to SellerClaw yet: the next manifest the cloud sends is built with the choice.
        return False, None
    outcome, err = manager.update_manifest(bundle_manifest_from_mapping(mapping))
    return outcome == "completed", err


def _restart(data_dir: Path, manager: SupervisorContainerManager) -> tuple[bool, str | None]:
    """Restart the gateway on the saved manifest. Its start script settles the ChatGPT sign-in
    (moves it to the store every agent reads, or removes it) while OpenClaw is stopped."""
    try:
        mapping = ManifestStorage(data_dir).load()
    except (OSError, ValueError) as exc:
        return False, str(exc)[:300]
    if mapping is None:
        return False, None
    outcome, err = manager.restart(bundle_manifest_from_mapping(mapping))
    return outcome == "completed", err


#: How long ``prepare`` waits for the gateway to load the ChatGPT provider after the config change.
PROVIDER_PLUGIN_WAIT_S = 60.0


async def _gateway_knows_openai() -> bool:
    from openclaw_diagnostics.gateway import GatewayConnection, GatewayError

    try:
        async with GatewayConnection(scopes=("operator.read",)) as gateway:
            result = await gateway.call("models.authStatus", {}, timeout=10.0)
    except (GatewayError, OSError, TimeoutError):
        return False
    return f'"{chatgpt_auth.OPENAI_PROVIDER}"' in json.dumps(result)


def _wait_for_openai_provider(timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if asyncio.run(_gateway_knows_openai()):
            return True
        time.sleep(2.0)
    return False


def _state(data_dir: Path, *, applied: bool = False, error: str | None = None) -> AiSubscriptionStateResponse:
    choice = load_choice(data_dir)
    monitor = get_ai_subscription_monitor()
    return AiSubscriptionStateResponse(
        provider=choice.provider if choice is not None else None,  # type: ignore[arg-type]
        status=monitor.latest_payload() if monitor is not None else None,
        offered=_lineup_offered(data_dir, choice.provider) if choice is not None else False,
        applied=applied,
        error=error,
    )


@owner_router.get("", response_model=AiSubscriptionStateResponse)
def get_ai_subscription(data_dir: Annotated[Path, Depends(_data_dir)]) -> AiSubscriptionStateResponse:
    return _state(data_dir)


@owner_router.post("/claude/activate", response_model=AiSubscriptionStateResponse)
def activate_claude(
    data_dir: Annotated[Path, Depends(_data_dir)],
    manager: Annotated[SupervisorContainerManager, Depends(_manager)],
) -> AiSubscriptionStateResponse:
    """After Claude Code's own sign-in: check it is a Claude plan, then switch the agent to it."""
    status = claude_cli.auth_status(data_dir)
    if not status.logged_in:
        raise HTTPException(
            status_code=409,
            detail={"code": "claude_not_signed_in", "message": "Claude is not signed in on this computer yet."},
        )
    if not status.on_subscription:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "claude_not_a_subscription",
                "message": "Claude is signed in with an API key, not a Claude plan. Sign in with your Claude account.",
            },
        )
    previous = load_choice(data_dir)
    save_choice(data_dir, "claude")
    chatgpt_auth.clear_login_pending(data_dir)
    switched_from = previous.provider if previous is not None and previous.provider != "claude" else None
    # Leaving ChatGPT takes a restart: its sign-in leaves OpenClaw's store only with the gateway
    # stopped. Claude Code itself needs just the new config.
    applied, err = (_restart if switched_from == "chatgpt" else _reapply)(data_dir, manager)
    if switched_from is not None and err is None:
        # One plan at a time: the other one is signed out only once this one is in place.
        _sign_out(data_dir, switched_from)
    monitor = get_ai_subscription_monitor()
    if monitor is not None:
        monitor.refresh_soon()
    return _state(data_dir, applied=applied, error=err)


def _sign_out(data_dir: Path, provider: str) -> None:
    """Sign the plan out on this machine. ChatGPT's profiles leave OpenClaw's store at the next
    gateway start (it must be stopped for that); its copy on the data volume goes now.

    Best effort: the switch it follows has already happened, so a sign-out that fails (Claude Code
    hanging past its timeout) is logged rather than turned into a failed request.
    """
    try:
        if provider == "claude":
            claude_cli.logout(data_dir)
        elif provider == "chatgpt":
            chatgpt_auth.forget(data_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("ai_subscription_sign_out_failed", provider=provider, error=type(exc).__name__)


def _state_dir() -> Path:
    return chatgpt_auth.openclaw_state_dir()


class ChatgptPrepareResponse(BaseModel):
    #: The gateway has loaded the ChatGPT provider, so its sign-in can start now.
    ready: bool
    error: str | None = None


@owner_router.post("/chatgpt/prepare", response_model=ChatgptPrepareResponse)
def prepare_chatgpt(
    data_dir: Annotated[Path, Depends(_data_dir)],
    manager: Annotated[SupervisorContainerManager, Depends(_manager)],
) -> ChatgptPrepareResponse:
    """Before the ChatGPT sign-in: load its provider plugin (models untouched) and wait for it."""
    chatgpt_auth.mark_login_pending(data_dir)
    _applied, err = _reapply(data_dir, manager)
    if err:
        return ChatgptPrepareResponse(ready=False, error=err)
    return ChatgptPrepareResponse(ready=_wait_for_openai_provider(PROVIDER_PLUGIN_WAIT_S))


@owner_router.post("/chatgpt/cancel", response_model=AiSubscriptionStateResponse)
def cancel_chatgpt(
    data_dir: Annotated[Path, Depends(_data_dir)],
    manager: Annotated[SupervisorContainerManager, Depends(_manager)],
) -> AiSubscriptionStateResponse:
    """The sign-in did not finish: stop holding the provider plugin on."""
    chatgpt_auth.clear_login_pending(data_dir)
    applied, err = _reapply(data_dir, manager)
    return _state(data_dir, applied=applied, error=err)


@owner_router.post("/chatgpt/activate", response_model=AiSubscriptionStateResponse)
def activate_chatgpt(
    data_dir: Annotated[Path, Depends(_data_dir)],
    manager: Annotated[SupervisorContainerManager, Depends(_manager)],
    state_dir: Annotated[Path, Depends(_state_dir)],
) -> AiSubscriptionStateResponse:
    """After OpenClaw's own ChatGPT sign-in: keep a copy, switch the agent to it, restart the gateway.

    The restart is what puts the sign-in in the store every agent reads (see ``chatgpt_auth``).
    """
    if not chatgpt_auth.has_sign_in(state_dir):
        raise HTTPException(
            status_code=409,
            detail={"code": "chatgpt_not_signed_in", "message": "ChatGPT is not signed in on this computer yet."},
        )
    chatgpt_auth.snapshot(data_dir, state_dir)
    previous = load_choice(data_dir)
    save_choice(data_dir, "chatgpt")
    chatgpt_auth.clear_login_pending(data_dir)
    applied, err = _restart(data_dir, manager)
    if previous is not None and previous.provider != "chatgpt" and err is None:
        # One plan at a time: the other one is signed out only once this one is in place — a
        # failed restart leaves the agent on its old config, and that one still works.
        _sign_out(data_dir, previous.provider)
    monitor = get_ai_subscription_monitor()
    if monitor is not None:
        monitor.refresh_soon()
    return _state(data_dir, applied=applied, error=err)


@owner_router.post("/disconnect", response_model=AiSubscriptionStateResponse)
def disconnect(
    data_dir: Annotated[Path, Depends(_data_dir)],
    manager: Annotated[SupervisorContainerManager, Depends(_manager)],
) -> AiSubscriptionStateResponse:
    """Back to SellerClaw credits: sign the plan out on this machine and rebuild the config."""
    choice = load_choice(data_dir)
    clear_choice(data_dir)
    # An abandoned `connect chatgpt` must not keep the ChatGPT provider loaded on credits.
    chatgpt_auth.clear_login_pending(data_dir)
    if choice is not None:
        _sign_out(data_dir, choice.provider)
    # ChatGPT's sign-in lives in OpenClaw's store, which only a stopped gateway lets go of: restart
    # so the sign-out is real now. Claude Code signed itself out above; a config reload is enough.
    if choice is not None and choice.provider == "chatgpt":
        applied, err = _restart(data_dir, manager)
    else:
        applied, err = _reapply(data_dir, manager)
    monitor = get_ai_subscription_monitor()
    if monitor is not None:
        monitor.refresh_soon()
    return _state(data_dir, applied=applied, error=err)


@plugin_router.post("/refresh", status_code=202)
def refresh_from_plugin() -> dict[str, bool]:
    """A turn just failed on the plan (limit, sign-in): look again now instead of within a minute."""
    monitor = get_ai_subscription_monitor()
    if monitor is not None:
        monitor.refresh_soon()
    return {"accepted": True}
