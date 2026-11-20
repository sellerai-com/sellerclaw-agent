"""Create a cloud scheduled task over the gateway's ``cron.add`` RPC, for the ``sellerclaw-ui`` plugin.

The plugin restores the owner's tasks and runs the cloud's "Create" command, but it does not add the
job through its service's scheduler handle any more: that handle normalizes the input like
``cron.add`` does, yet skips the step only the RPC takes for a caller — recording which inference
account the job runs on (``captureCronInferenceForCaller``, OpenClaw 2026.11.x). On a machine whose
state holds a stored account (``litellm:default``, written by older doctor migrations) OpenClaw then
refuses every run of such a job: "Automation has no recorded inference account". The RPC records
that account where one is stored, and leaves the job on the environment key where none is (a cloud
machine, ``openclaw_start``: ``LITELLM_API_KEY``) — what a task created in chat with
``openclaw cron add`` gets.

So the plugin asks this agent server (``POST /internal/openclaw/cron/add``) and the job goes over an
``operator.admin`` connection, like "Run now" (``cron_commands``). ``declarationKey`` keeps a repeated
add harmless: the gateway answers with the job it already has.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Final

import structlog
from openclaw_diagnostics.gateway import GatewayConnection, GatewayError, GatewayUnreachableError

from sellerclaw_agent.cloud.cron_runs_backfill import CronGateway

_log = structlog.get_logger(__name__)

#: Only jobs the cloud owns go through here; a foreign key could overwrite a system job.
SELLERCLAW_DECLARATION_PREFIX: Final[str] = "sellerclaw:job:"
#: ``cron.add`` with the caller's inference capture needs an operator connection; admin like ``cron.run``.
CRON_ADD_SCOPES: Final[tuple[str, ...]] = ("operator.admin",)
_RPC_TIMEOUT_S: Final[float] = 30.0
_MAX_ERROR_CHARS: Final[int] = 2000

AddGatewayFactory = Callable[[], AbstractAsyncContextManager[CronGateway]]


def _default_add_gateway() -> AbstractAsyncContextManager[CronGateway]:
    return GatewayConnection(scopes=CRON_ADD_SCOPES)


def add_result(*, ok: bool, job: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    return {"ok": ok, "job": job, "error": error[:_MAX_ERROR_CHARS] if error else None}


async def add_cron_job(
    job: dict[str, Any],
    *,
    gateway_factory: AddGatewayFactory | None = None,
) -> dict[str, Any]:
    """Add ``job`` over ``cron.add``; ``{ok, job, error}`` with the job as the gateway stored it."""
    key = job.get("declarationKey")
    if not isinstance(key, str) or not key.startswith(SELLERCLAW_DECLARATION_PREFIX):
        return add_result(ok=False, error=f"add needs a declarationKey starting with {SELLERCLAW_DECLARATION_PREFIX}")
    try:
        async with (gateway_factory or _default_add_gateway)() as gateway:
            outcome = await gateway.call("cron.add", job, timeout=_RPC_TIMEOUT_S)
    except GatewayUnreachableError:
        return add_result(ok=False, error="The assistant is starting; try again in a minute.")
    except GatewayError as exc:
        _log.warning("cron_add_refused", declaration_key=key, error=str(exc)[:300])
        return add_result(ok=False, error=str(exc))
    stored = outcome.get("job") if isinstance(outcome, dict) else None
    if not isinstance(stored, dict) or not stored.get("id"):
        return add_result(ok=False, error=f"The gateway gave an unreadable answer: {str(outcome)[:200]}")
    return add_result(ok=True, job=stored)
