from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sellerclaw_agent.cloud.cron_reporter import read_cron_snapshot, run_cron_reporter_loop
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit

_JOB = {
    "id": "dbfa2928-244e-4dcf-80fa-edfbea8caa43",
    "agentId": "supervisor",
    "sessionKey": "agent:supervisor:sellerclaw-ui:direct:8ac2e73f-3b47-4e4d-ac13-68226a03b72a",
    "name": "Daily Shopify Orders Report",
    "description": "Check orders every day at 8:00 UTC",
    "enabled": True,
    "createdAtMs": 1779474213878,
    "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "UTC"},
    "payload": {"kind": "agentTurn", "message": "Check orders for the last 24 hours."},
    "delivery": {
        "mode": "announce",
        "to": "sellerclaw-ui:direct:8ac2e73f-3b47-4e4d-ac13-68226a03b72a",
        "channel": "sellerclaw-ui",
        "accountId": "default",
    },
}
_STATE = {
    "version": 1,
    "jobs": {"dbfa2928-244e-4dcf-80fa-edfbea8caa43": {"state": {"nextRunAtMs": 1779523200000}}},
}


def _write_cron_files(state_dir: Path, *, jobs: list[dict], state: dict | None = None) -> None:
    cron_dir = state_dir / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "jobs.json").write_text(json.dumps({"version": 1, "jobs": jobs}), encoding="utf-8")
    if state is not None:
        (cron_dir / "jobs-state.json").write_text(json.dumps(state), encoding="utf-8")


def test_read_cron_snapshot_merges_jobs_and_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "oc"
    _write_cron_files(state_dir, jobs=[_JOB], state=_STATE)

    snapshot = read_cron_snapshot(state_dir)

    assert len(snapshot["jobs"]) == 1
    job = snapshot["jobs"][0]
    assert job["openclaw_job_id"] == "dbfa2928-244e-4dcf-80fa-edfbea8caa43"
    assert job["name"] == "Daily Shopify Orders Report"
    assert job["schedule_expr"] == "0 8 * * *"
    assert job["schedule_tz"] == "UTC"
    assert job["enabled"] is True
    assert job["payload_message"] == "Check orders for the last 24 hours."
    assert job["delivery_to"] == "sellerclaw-ui:direct:8ac2e73f-3b47-4e4d-ac13-68226a03b72a"
    assert job["session_key"] == "agent:supervisor:sellerclaw-ui:direct:8ac2e73f-3b47-4e4d-ac13-68226a03b72a"
    assert job["agent_id"] == "supervisor"
    # epoch ms converted to ISO-8601 UTC
    assert job["cron_created_at"] is not None and job["cron_created_at"].endswith("+00:00")
    assert job["next_run_at"] is not None and job["next_run_at"].endswith("+00:00")


def test_read_cron_snapshot_missing_files_returns_empty(tmp_path: Path) -> None:
    snapshot = read_cron_snapshot(tmp_path / "does-not-exist")
    assert snapshot["jobs"] == []
    assert "snapshot_hash" in snapshot


def test_read_cron_snapshot_job_without_state_row_has_no_next_run(tmp_path: Path) -> None:
    state_dir = tmp_path / "oc"
    _write_cron_files(state_dir, jobs=[_JOB], state={"version": 1, "jobs": {}})

    job = read_cron_snapshot(state_dir)["jobs"][0]
    assert job["next_run_at"] is None
    assert job["cron_created_at"] is not None


def test_read_cron_snapshot_hash_stable_across_captured_at(tmp_path: Path) -> None:
    state_dir = tmp_path / "oc"
    _write_cron_files(state_dir, jobs=[_JOB], state=_STATE)

    first = read_cron_snapshot(state_dir)
    second = read_cron_snapshot(state_dir)

    assert first["snapshot_hash"] == second["snapshot_hash"]
    # captured_at is independent of the content hash
    assert "captured_at" in first and "captured_at" in second


def test_read_cron_snapshot_hash_changes_when_jobs_change(tmp_path: Path) -> None:
    state_dir = tmp_path / "oc"
    _write_cron_files(state_dir, jobs=[_JOB], state=_STATE)
    before = read_cron_snapshot(state_dir)["snapshot_hash"]

    disabled = {**_JOB, "enabled": False}
    _write_cron_files(state_dir, jobs=[disabled], state=_STATE)
    after = read_cron_snapshot(state_dir)["snapshot_hash"]

    assert before != after


async def test_cron_reporter_pushes_initial_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path / "data"))
    state_dir = tmp_path / "oc"
    _write_cron_files(state_dir, jobs=[_JOB], state=_STATE)
    monkeypatch.setattr(
        "sellerclaw_agent.cloud.cron_reporter.resolve_agent_bearer_token",
        lambda _creds: "tok",
    )

    async def _fake_awatch(*_args: object, **_kwargs: object):
        if False:
            yield set()

    monkeypatch.setattr("sellerclaw_agent.cloud.cron_reporter.awatch", _fake_awatch)

    client = MagicMock()
    client.report_cron_jobs = AsyncMock(return_value=True)
    stop = asyncio.Event()

    await asyncio.wait_for(
        run_cron_reporter_loop(
            stop,
            registry=EdgeRuntimeRegistry(),
            state_dir=state_dir,
            client=client,
        ),
        timeout=2.0,
    )

    assert client.report_cron_jobs.await_count == 1
    payload = client.report_cron_jobs.await_args.args[0]
    assert [j["openclaw_job_id"] for j in payload["jobs"]] == ["dbfa2928-244e-4dcf-80fa-edfbea8caa43"]


async def test_cron_reporter_skips_push_when_not_authenticated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path / "data"))
    state_dir = tmp_path / "oc"
    _write_cron_files(state_dir, jobs=[_JOB], state=_STATE)
    monkeypatch.setattr(
        "sellerclaw_agent.cloud.cron_reporter.resolve_agent_bearer_token",
        lambda _creds: None,
    )

    async def _fake_awatch(*_args: object, **_kwargs: object):
        if False:
            yield set()

    monkeypatch.setattr("sellerclaw_agent.cloud.cron_reporter.awatch", _fake_awatch)

    client = MagicMock()
    client.report_cron_jobs = AsyncMock(return_value=True)
    stop = asyncio.Event()

    await asyncio.wait_for(
        run_cron_reporter_loop(
            stop,
            registry=EdgeRuntimeRegistry(),
            state_dir=state_dir,
            client=client,
        ),
        timeout=2.0,
    )

    assert client.report_cron_jobs.await_count == 0
