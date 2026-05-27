"""Report OpenClaw cron jobs to the cloud: on file change + every ~30 min.

OpenClaw has no cron lifecycle hooks, so we detect changes by watching the
cron state files (``$OPENCLAW_STATE_DIR/cron/jobs.json`` + ``jobs-state.json``)
and also push periodically as a freshness/liveness fallback. The agent pushes
outbound, which works for self-hosted runtimes the cloud cannot reach directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from watchfiles import Change, awatch

from sellerclaw_agent.async_backoff import sleep_until
from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token
from sellerclaw_agent.cloud.connection_client import SellerClawConnectionClient
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import (
    CloudAuthError,
    CloudConnectionError,
    CloudSessionInvalidatedError,
)
from sellerclaw_agent.cloud.state_backup import default_openclaw_state_dir
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

_log = structlog.get_logger(__name__)

CRON_REPORT_PERIODIC_SECONDS = float(os.environ.get("CRON_REPORT_PERIODIC_SECONDS") or 1800.0)
_DEBOUNCE_MS = 1600
_WAIT_FOR_STATE_DIR_SECONDS = 30.0
_CRON_FILES = {"jobs.json", "jobs-state.json"}
_MS_MINUTE = 60_000
_MS_HOUR = 3_600_000
_MS_DAY = 86_400_000


def _positive_int_ms(value: Any) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    ms = int(value)
    return ms if ms > 0 else None


def _schedule_expr_from_openclaw(schedule: dict[str, Any]) -> str:
    """Map an OpenClaw schedule block to a 5-field cron or ``every:<ms>`` for the UI."""
    kind = schedule.get("kind")
    if kind == "cron":
        return str(schedule.get("expr") or "")
    if kind != "every":
        return ""

    every_ms = _positive_int_ms(schedule.get("everyMs"))
    if every_ms is None:
        return ""

    if every_ms % _MS_MINUTE == 0:
        minutes = every_ms // _MS_MINUTE
        if minutes == 1:
            return "* * * * *"
        if 2 <= minutes <= 59:
            return f"*/{minutes} * * * *"
        if minutes == 60:
            return "0 * * * *"

    if every_ms % _MS_HOUR == 0:
        hours = every_ms // _MS_HOUR
        if hours == 1:
            return "0 * * * *"
        if 2 <= hours <= 23:
            return f"0 */{hours} * * *"

    if every_ms % _MS_DAY == 0:
        days = every_ms // _MS_DAY
        if days == 1:
            return "0 0 * * *"
        if 2 <= days <= 31:
            return f"0 0 */{days} * *"

    return f"every:{every_ms}"


def _ms_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_cron_snapshot(state_dir: Path) -> dict[str, Any]:
    """Merge ``cron/jobs.json`` + ``cron/jobs-state.json`` into a cloud payload.

    Tolerates missing/partial files (returns an empty ``jobs`` list). ``snapshot_hash``
    is computed over the jobs only (not ``captured_at``) so callers can dedup pushes.
    """
    cron_dir = state_dir / "cron"
    jobs_doc = _load_json(cron_dir / "jobs.json")
    state_doc = _load_json(cron_dir / "jobs-state.json")
    state_jobs = _as_dict(state_doc.get("jobs"))

    jobs: list[dict[str, Any]] = []
    raw_jobs = jobs_doc.get("jobs")
    for raw in raw_jobs if isinstance(raw_jobs, list) else []:
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("id")
        if not job_id:
            continue
        schedule = _as_dict(raw.get("schedule"))
        delivery = _as_dict(raw.get("delivery"))
        payload = _as_dict(raw.get("payload"))
        st_state = _as_dict(_as_dict(state_jobs.get(str(job_id))).get("state"))
        jobs.append(
            {
                "openclaw_job_id": str(job_id),
                "name": str(raw.get("name") or ""),
                "description": str(raw.get("description") or ""),
                "enabled": bool(raw.get("enabled", True)),
                "schedule_expr": _schedule_expr_from_openclaw(schedule),
                "schedule_tz": str(schedule.get("tz") or "UTC"),
                "payload_message": str(payload.get("message") or ""),
                "delivery_channel": _opt_str(delivery.get("channel")),
                "delivery_to": _opt_str(delivery.get("to")),
                "agent_id": _opt_str(raw.get("agentId")),
                "session_key": _opt_str(raw.get("sessionKey")),
                "cron_created_at": _ms_to_iso(raw.get("createdAtMs")),
                "next_run_at": _ms_to_iso(st_state.get("nextRunAtMs")),
            }
        )

    snapshot_hash = hashlib.sha256(
        json.dumps(jobs, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "snapshot_hash": snapshot_hash,
        "jobs": jobs,
    }


def _is_cron_file(_change: Change, path: str) -> bool:
    candidate = Path(path)
    return candidate.name in _CRON_FILES and candidate.parent.name == "cron"


async def run_cron_reporter_loop(
    stop: asyncio.Event,
    *,
    registry: EdgeRuntimeRegistry,
    state_dir: Path | None = None,
    client: SellerClawConnectionClient | None = None,
) -> None:
    """Push the current cron-job set to the cloud on change and every ~30 min."""
    _ = registry  # restart accounting handled by the watchdog wrapper
    data_dir = Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    creds_storage = CredentialsStorage(data_dir)
    cloud = client or SellerClawConnectionClient(credentials_storage=creds_storage)
    sd = state_dir or default_openclaw_state_dir()
    loop = asyncio.get_running_loop()

    last_hash: str | None = None
    last_push = 0.0

    async def _maybe_push(*, force: bool) -> None:
        nonlocal last_hash, last_push
        if resolve_agent_bearer_token(creds_storage) is None:
            return
        payload = await loop.run_in_executor(None, read_cron_snapshot, sd)
        now = time.monotonic()
        changed = payload["snapshot_hash"] != last_hash
        stale = (now - last_push) >= CRON_REPORT_PERIODIC_SECONDS
        if not (force or changed or stale):
            return
        try:
            await cloud.report_cron_jobs(payload)
        except (CloudAuthError, CloudSessionInvalidatedError, CloudConnectionError) as exc:
            _log.warning("cron_report_failed", error=str(exc)[:300])
            return
        last_hash = payload["snapshot_hash"]
        last_push = now
        _log.info("cron_report_pushed", jobs=len(payload["jobs"]))

    # OpenClaw may still be creating its state dir when the agent boots.
    while not sd.exists() and not stop.is_set():
        await sleep_until(stop, _WAIT_FOR_STATE_DIR_SECONDS)
    if stop.is_set():
        return

    await _maybe_push(force=True)

    async for _changes in awatch(
        sd,
        watch_filter=_is_cron_file,
        stop_event=stop,
        yield_on_timeout=True,
        rust_timeout=int(CRON_REPORT_PERIODIC_SECONDS * 1000),
        debounce=_DEBOUNCE_MS,
    ):
        if stop.is_set():
            break
        await _maybe_push(force=False)
