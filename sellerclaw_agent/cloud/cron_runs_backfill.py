"""Backfill recent OpenClaw cron runs into the cloud's run history.

The ``sellerclaw-ui`` plugin reports each ``finished`` event as it happens, but a report can be
lost (the cloud was unreachable, the gateway restarted mid-post), the hook's event carries no
token ``usage``, and the plugin's scheduler handle has no history read. So the plugin asks this
agent server (``POST /internal/openclaw/cron/backfill-runs``) to read runs over the gateway's
``cron.runs`` RPC (``operator.read``) and post them to ``/agent/cron/runs``: the last runs of every
mirrored job after each reconciliation (and after a run report it had to give up on), and the
latest runs of one job right after it finished (``{"job_ids": [...]}``), which completes that run
with its token usage. The cloud keys runs on ``(user, jobId, runAtMs)`` and merges
repeats, so re-sending a run it already has is harmless.

Mirrored jobs are the ones the cloud keeps a copy of: no ``declarationKey`` or a
``sellerclaw:job:`` one, and not a system ``heartbeat`` payload (same rule as the plugin).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Final, Protocol

import structlog
from openclaw_diagnostics.gateway import GatewayConnection, GatewayError

from sellerclaw_agent.cloud.connection_client import SellerClawConnectionClient
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.cron_standby import CronStandby, get_cron_standby
from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudConnectionError

_log = structlog.get_logger(__name__)

SELLERCLAW_DECLARATION_PREFIX: Final[str] = "sellerclaw:job:"
#: ``cron.list`` / ``cron.runs`` are read methods.
CRON_READ_SCOPES: Final[tuple[str, ...]] = ("operator.read",)
RUNS_PER_JOB: Final[int] = 20
#: Right after a run finished: its own entry, plus a little slack for runs close together.
RUNS_PER_FINISHED_JOB: Final[int] = 3
#: Most job ids one targeted request may name.
MAX_TARGETED_JOB_IDS: Final[int] = 50
#: ``cron.list`` pages at most 200 jobs; the cloud mirrors at most 200 either.
_LIST_PAGE_LIMIT: Final[int] = 200
_MAX_LIST_PAGES: Final[int] = 5
#: Server-side cap of one ``/agent/cron/runs`` request.
MAX_RUNS_PER_POST: Final[int] = 100
_CALL_TIMEOUT_S: Final[float] = 30.0


class CronGateway(Protocol):
    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any: ...


class CronRunsCloud(Protocol):
    async def post_cron_runs(self, runs: list[dict[str, Any]]) -> bool: ...


GatewayFactory = Callable[[], AbstractAsyncContextManager[CronGateway]]


def default_gateway_factory() -> AbstractAsyncContextManager[CronGateway]:
    return GatewayConnection(scopes=CRON_READ_SCOPES)


def is_mirrored_job(job: Any) -> bool:
    if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"].strip():
        return False
    payload = job.get("payload")
    if isinstance(payload, dict) and payload.get("kind") == "heartbeat":
        return False
    key = job.get("declarationKey")
    if key is None or key == "":
        return True
    return isinstance(key, str) and key.startswith(SELLERCLAW_DECLARATION_PREFIX)


async def list_mirrored_jobs(gateway: CronGateway) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_MAX_LIST_PAGES):
        page = await gateway.call(
            "cron.list",
            {
                "includeDisabled": True,
                "limit": _LIST_PAGE_LIMIT,
                "offset": offset,
                "includeDeliveryPreviews": False,
            },
            timeout=_CALL_TIMEOUT_S,
        )
        items = page.get("jobs") if isinstance(page, dict) else page
        jobs.extend(job for job in (items if isinstance(items, list) else []) if is_mirrored_job(job))
        next_offset = page.get("nextOffset") if isinstance(page, dict) else None
        if not (isinstance(page, dict) and page.get("hasMore") is True and isinstance(next_offset, int)):
            break
        offset = next_offset
    return jobs


async def collect_recent_runs(
    gateway: CronGateway,
    *,
    per_job: int = RUNS_PER_JOB,
    job_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """The last ``per_job`` finished runs of every mirrored job (or of ``job_ids``), as ``cron.runs`` returns them.

    ``job_ids`` come from the plugin, which only names jobs it mirrors.
    """
    runs: list[dict[str, Any]] = []
    jobs = [{"id": job_id} for job_id in job_ids] if job_ids is not None else await list_mirrored_jobs(gateway)
    for job in jobs:
        job_id = job["id"]
        try:
            page = await gateway.call("cron.runs", {"id": job_id, "limit": per_job}, timeout=_CALL_TIMEOUT_S)
        except GatewayError as exc:
            # A job removed between the two reads: skip it, keep the others.
            _log.info("cron_runs_backfill_job_skipped", job_id=job_id, error=str(exc)[:300])
            continue
        entries = page.get("entries") if isinstance(page, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get("action", "finished") != "finished":
                continue
            if entry.get("jobId") != job_id or not isinstance(entry.get("runAtMs"), int):
                continue
            if "jobName" not in entry and isinstance(job.get("name"), str):
                entry = {**entry, "jobName": job["name"]}
            runs.append(entry)
    return runs


async def backfill_cron_runs(
    *,
    gateway_factory: GatewayFactory,
    cloud: CronRunsCloud,
    standby: CronStandby,
    job_ids: list[str] | None = None,
) -> int:
    """Read recent runs and post them in server-sized chunks; returns how many were accepted."""
    if standby.is_standby():
        return 0
    per_job = RUNS_PER_JOB if job_ids is None else RUNS_PER_FINISHED_JOB
    async with gateway_factory() as gateway:
        runs = await collect_recent_runs(gateway, per_job=per_job, job_ids=job_ids)
    posted = 0
    for start in range(0, len(runs), MAX_RUNS_PER_POST):
        if standby.is_standby():
            break
        chunk = runs[start : start + MAX_RUNS_PER_POST]
        if not await cloud.post_cron_runs(chunk):
            _log.debug("cron_runs_backfill_endpoint_missing")
            break
        posted += len(chunk)
    return posted


class CronRunsBackfill:
    """Runs backfills one at a time; requests during a run are merged into exactly one more."""

    def __init__(
        self,
        *,
        data_dir: Path,
        standby: CronStandby | None = None,
        gateway_factory: GatewayFactory = default_gateway_factory,
        cloud_factory: Callable[[], CronRunsCloud] | None = None,
    ) -> None:
        self._standby = standby or get_cron_standby(data_dir)
        self._gateway_factory = gateway_factory
        self._cloud_factory: Callable[[], CronRunsCloud] = cloud_factory or (
            lambda: SellerClawConnectionClient(credentials_storage=CredentialsStorage(data_dir))
        )
        self._task: asyncio.Task[None] | None = None
        self._pending_all = False
        self._pending_ids: set[str] = set()

    def request(self, job_ids: list[str] | None = None) -> None:
        """Backfill every mirrored job (``None``) or just the named ones."""
        if job_ids is None:
            self._pending_all = True
        else:
            self._pending_ids.update(job_id for job_id in job_ids if job_id)
            if not self._pending_ids and not self._pending_all:
                return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="cron_runs_backfill")

    async def _run(self) -> None:
        while self._pending_all or self._pending_ids:
            # A full pass covers the named jobs too.
            job_ids = None if self._pending_all else sorted(self._pending_ids)
            self._pending_all = False
            self._pending_ids = set()
            try:
                posted = await backfill_cron_runs(
                    gateway_factory=self._gateway_factory,
                    cloud=self._cloud_factory(),
                    standby=self._standby,
                    job_ids=job_ids,
                )
                _log.info("cron_runs_backfilled", runs=posted, job_ids=job_ids)
            except (GatewayError, CloudAuthError, CloudConnectionError) as exc:
                _log.warning("cron_runs_backfill_failed", error=str(exc)[:300])
            except Exception:  # noqa: BLE001 - a background job must not die silently
                _log.exception("cron_runs_backfill_crashed")

    async def wait_idle(self) -> None:
        while self._task is not None and not self._task.done():
            await asyncio.gather(self._task, return_exceptions=True)


_BACKFILL: CronRunsBackfill | None = None


def get_cron_runs_backfill() -> CronRunsBackfill:
    global _BACKFILL
    if _BACKFILL is None:
        _BACKFILL = CronRunsBackfill(data_dir=Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data")))
    return _BACKFILL
