"""``cron_command`` frames from the cloud: change the owner's OpenClaw cron jobs, report the outcome.

The cloud's scheduled-tasks page (pause / resume / edit / delete / run now, and Create in the
wizard) sends one frame per action on the agent's event stream:
``{request_id, user_id, action: add|update|remove|run, job_id, job, patch}``. The
``sellerclaw-ui`` plugin executes it through the gateway's scheduler (``cron-sync.ts``: the plugin
service's ``ctx.getCron()`` normalizes input exactly like the ``cron.add`` RPC); this module hands
the frame over and posts the outcome to ``POST /agent/cron/commands/{request_id}``.

Each frame runs as its own task, so the stream keeps flowing while a command waits on the
gateway; commands still execute one at a time, in arrival order. A repeated ``request_id`` is
dropped. A replaced instance (cron standby) neither executes nor reports anything.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Protocol

import httpx
import structlog

from sellerclaw_agent.cloud.connection_client import SellerClawConnectionClient
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.cron_standby import CronStandby
from sellerclaw_agent.cloud.edge_sse_common import OpenClawGate
from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudConnectionError
from sellerclaw_agent.cloud.openclaw_forwarder import LocalOpenClawForwarder, openclaw_gateway_base_url
from sellerclaw_agent.server.secrets_store import get_secrets

_log = structlog.get_logger(__name__)

#: The frame fields the plugin's command route reads; anything else stays on the cloud side.
_FRAME_FIELDS: Final[tuple[str, ...]] = ("request_id", "action", "job_id", "job", "patch")
_ACTIONS: Final[frozenset[str]] = frozenset({"add", "update", "remove", "run"})
_MAX_ERROR_CHARS: Final[int] = 2000


class CronCommandPlugin(Protocol):
    async def post_cron_command_json(self, body: dict[str, Any]) -> dict[str, Any]: ...


class CronCommandCloud(Protocol):
    async def post_cron_command_result(self, *, request_id: str, result: dict[str, Any]) -> bool: ...


def _default_plugin_factory(data_dir: Path) -> Callable[[], CronCommandPlugin]:
    def _factory() -> CronCommandPlugin:
        # No shared HTTP client: a command task may outlive the stream connection that delivered it.
        secrets = get_secrets(data_dir)
        return LocalOpenClawForwarder(
            base_url=openclaw_gateway_base_url(),
            hooks_token=secrets.hooks_token,
            gateway_token=secrets.gateway_token,
        )

    return _factory


def _default_cloud_factory(data_dir: Path) -> Callable[[], CronCommandCloud]:
    def _factory() -> CronCommandCloud:
        return SellerClawConnectionClient(credentials_storage=CredentialsStorage(data_dir))

    return _factory


def command_result(
    *,
    ok: bool,
    error: str | None = None,
    store_epoch: str | None = None,
    job: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """The body of ``POST /agent/cron/commands/{request_id}``."""
    return {
        "ok": ok,
        "error": error[:_MAX_ERROR_CHARS] if error else None,
        "store_epoch": store_epoch,
        "job": job,
        "run_id": run_id,
    }


def _result_from_plugin(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only the contract's fields from the plugin's answer, typed as the cloud expects."""
    error = payload.get("error")
    store_epoch = payload.get("store_epoch")
    job = payload.get("job")
    run_id = payload.get("run_id")
    return command_result(
        ok=payload.get("ok") is True,
        error=error if isinstance(error, str) and error else None,
        store_epoch=store_epoch if isinstance(store_epoch, str) and store_epoch else None,
        job=job if isinstance(job, dict) else None,
        run_id=run_id if isinstance(run_id, str) and run_id else None,
    )


class CronCommandDispatcher:
    """Runs ``cron_command`` frames off the stream loop, one at a time, each request id once."""

    def __init__(
        self,
        *,
        data_dir: Path,
        standby: CronStandby,
        openclaw_gate: OpenClawGate | None = None,
        plugin_factory: Callable[[], CronCommandPlugin] | None = None,
        cloud_factory: Callable[[], CronCommandCloud] | None = None,
        remember: int = 1024,
    ) -> None:
        self._standby = standby
        self._gate = openclaw_gate
        self._plugin_factory = plugin_factory or _default_plugin_factory(data_dir)
        self._cloud_factory = cloud_factory or _default_cloud_factory(data_dir)
        self._remember = remember
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, payload: dict[str, Any]) -> asyncio.Task[None] | None:
        """Start handling one frame in the background; ``None`` when it is dropped right away."""
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            _log.warning("cron_command_missing_request_id", action=payload.get("action"))
            return None
        if request_id in self._seen:
            self._seen.move_to_end(request_id)
            _log.info("cron_command_duplicate_dropped", request_id=request_id)
            return None
        self._seen[request_id] = None
        while len(self._seen) > self._remember:
            self._seen.popitem(last=False)
        task = asyncio.create_task(self._handle(request_id, payload), name=f"cron_command:{request_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _handle(self, request_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            if self._standby.is_standby():
                # Replaced by another instance: that one answers the owner, this one stays silent.
                _log.info("cron_command_ignored_on_standby", request_id=request_id)
                return
            try:
                result = await self._execute(request_id, payload)
            except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the dispatcher
                _log.exception("cron_command_failed_unexpectedly", request_id=request_id)
                result = command_result(ok=False, error=f"The assistant could not run the command: {exc}")
            await self._report(request_id, result)

    async def _execute(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        if action not in _ACTIONS:
            return command_result(ok=False, error=f"unknown action: {action or '(none)'}")
        if self._gate is not None:
            running, status, _err = await self._gate()
            if not running:
                return command_result(ok=False, error=f"The assistant is not running ({status}).")
        frame = {key: payload.get(key) for key in _FRAME_FIELDS}
        try:
            answer = await self._plugin_factory().post_cron_command_json(frame)
        except httpx.ConnectError:
            return command_result(ok=False, error="The assistant is starting; try again in a minute.")
        except httpx.TimeoutException:
            return command_result(ok=False, error="The assistant did not answer in time.")
        except httpx.HTTPStatusError as exc:
            return command_result(
                ok=False, error=f"The assistant refused the command (HTTP {exc.response.status_code})."
            )
        except ValueError as exc:
            return command_result(ok=False, error=f"The assistant gave an unreadable answer: {exc}")
        result = _result_from_plugin(answer)
        _log.info("cron_command_done", request_id=request_id, action=action, ok=result["ok"], error=result["error"])
        return result

    async def _report(self, request_id: str, result: dict[str, Any]) -> None:
        if self._standby.is_standby():
            return
        try:
            accepted = await self._cloud_factory().post_cron_command_result(request_id=request_id, result=result)
        except (CloudAuthError, CloudConnectionError) as exc:
            _log.warning("cron_command_result_not_reported", request_id=request_id, error=str(exc)[:300])
            return
        if not accepted:
            _log.debug("cron_command_result_endpoint_missing", request_id=request_id)

    async def wait_idle(self) -> None:
        """Wait for every submitted frame to finish (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
