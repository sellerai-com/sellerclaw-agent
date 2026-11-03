from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from openclaw_diagnostics.gateway import GatewayError

from sellerclaw_agent.cloud.cron_runs_backfill import (
    CronRunsBackfill,
    backfill_cron_runs,
    collect_recent_runs,
    is_mirrored_job,
)
from sellerclaw_agent.cloud.cron_standby import CronStandby

pytestmark = pytest.mark.unit


def _job(job_id: str, **overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": job_id,
        "name": f"Job {job_id}",
        "enabled": True,
        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
        "payload": {"kind": "agentTurn", "message": "do it"},
    }
    job.update(overrides)
    return job


def _run(job_id: str, run_at_ms: int, **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ts": run_at_ms + 5000,
        "jobId": job_id,
        "action": "finished",
        "status": "ok",
        "summary": "done",
        "runAtMs": run_at_ms,
        "durationMs": 5000,
        "sessionKey": f"agent:supervisor:cron:{job_id}:run:x",
    }
    entry.update(overrides)
    return entry


class _FakeGateway:
    """``cron.list`` pages and ``cron.runs`` answers keyed by job id; records every call."""

    def __init__(self, pages: list[dict[str, Any]], runs: dict[str, list[dict[str, Any]] | Exception]) -> None:
        self.pages = pages
        self.runs = runs
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.opened = 0

    def factory(self) -> _FakeGateway:
        return self

    async def __aenter__(self) -> _FakeGateway:
        self.opened += 1
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        params = params or {}
        self.calls.append((method, params))
        if method == "cron.list":
            index = 0 if not params.get("offset") else 1
            return self.pages[index]
        if method == "cron.runs":
            answer = self.runs.get(params["id"], [])
            if isinstance(answer, Exception):
                raise answer
            return {"entries": answer, "total": len(answer)}
        raise AssertionError(f"unexpected method {method}")


class _FakeCloud:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.posts: list[list[dict[str, Any]]] = []

    async def post_cron_runs(self, runs: list[dict[str, Any]]) -> bool:
        self.posts.append(runs)
        return self.accepted


class _NullStandbyPlugin:
    async def post_cron_standby_json(self, *, standby: bool) -> tuple[bool, dict[str, Any]]:
        return True, {}


def _standby(tmp_path: Path) -> CronStandby:
    return CronStandby(tmp_path, plugin_factory=_NullStandbyPlugin)


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        pytest.param(_job("a"), True, id="no-declaration-key"),
        pytest.param(_job("a", declarationKey="sellerclaw:job:1"), True, id="our-namespace"),
        pytest.param(_job("a", declarationKey="memory-core:dreaming"), False, id="foreign-namespace"),
        pytest.param(
            _job("a", declarationKey="heartbeat:supervisor", payload={"kind": "heartbeat"}), False, id="heartbeat"
        ),
        pytest.param(_job("a", payload={"kind": "heartbeat"}), False, id="heartbeat-payload"),
        pytest.param({"name": "no id"}, False, id="no-id"),
    ],
)
def test_is_mirrored_job(job: dict[str, Any], expected: bool) -> None:
    assert is_mirrored_job(job) is expected


async def test_collects_the_finished_runs_of_mirrored_jobs_only() -> None:
    gateway = _FakeGateway(
        pages=[
            {
                "jobs": [
                    _job("ours", declarationKey="sellerclaw:job:1"),
                    _job("legacy"),
                    _job("dream", declarationKey="memory-core:dreaming"),
                ],
                "hasMore": False,
            }
        ],
        runs={
            "ours": [_run("ours", 2000), _run("ours", 1000, action="started"), _run("other", 3000)],
            "legacy": [_run("legacy", 4000, jobName="Old name"), {"jobId": "legacy", "runAtMs": "x"}],
        },
    )

    runs = await collect_recent_runs(gateway, per_job=20)

    assert runs == [
        {**_run("ours", 2000), "jobName": "Job ours"},
        _run("legacy", 4000, jobName="Old name"),
    ]
    assert gateway.calls == [
        ("cron.list", {"includeDisabled": True, "limit": 200, "offset": 0, "includeDeliveryPreviews": False}),
        ("cron.runs", {"id": "ours", "limit": 20}),
        ("cron.runs", {"id": "legacy", "limit": 20}),
    ]


async def test_follows_cron_list_pages() -> None:
    gateway = _FakeGateway(
        pages=[
            {"jobs": [_job("a")], "hasMore": True, "nextOffset": 1},
            {"jobs": [_job("b")], "hasMore": False},
        ],
        runs={"a": [_run("a", 1)], "b": [_run("b", 2)]},
    )
    runs = await collect_recent_runs(gateway)
    assert [r["jobId"] for r in runs] == ["a", "b"]
    assert [p["offset"] for m, p in gateway.calls if m == "cron.list"] == [0, 1]


async def test_a_job_gone_between_the_reads_is_skipped() -> None:
    gateway = _FakeGateway(
        pages=[{"jobs": [_job("gone"), _job("kept")], "hasMore": False}],
        runs={"gone": GatewayError("cron.runs failed: NOT_FOUND: unknown job"), "kept": [_run("kept", 7)]},
    )
    runs = await collect_recent_runs(gateway)
    assert [r["jobId"] for r in runs] == ["kept"]


async def test_a_targeted_backfill_reads_only_the_named_jobs(tmp_path: Path) -> None:
    """Right after a run finished: that job's latest runs (with token usage), no cron.list."""
    usage = {"input_tokens": 1200, "output_tokens": 300}
    gateway = _FakeGateway(pages=[], runs={"ours": [_run("ours", 5000, usage=usage), _run("ours", 4000)]})
    cloud = _FakeCloud()

    posted = await backfill_cron_runs(
        gateway_factory=gateway.factory, cloud=cloud, standby=_standby(tmp_path), job_ids=["ours"]
    )

    assert posted == 2
    assert gateway.calls == [("cron.runs", {"id": "ours", "limit": 3})]
    assert cloud.posts == [[_run("ours", 5000, usage=usage), _run("ours", 4000)]]


async def test_backfill_posts_in_server_sized_chunks(tmp_path: Path) -> None:
    jobs = [_job(f"j{i}") for i in range(6)]
    gateway = _FakeGateway(
        pages=[{"jobs": jobs, "hasMore": False}],
        runs={job["id"]: [_run(job["id"], 1000 + n) for n in range(20)] for job in jobs},
    )
    cloud = _FakeCloud()

    posted = await backfill_cron_runs(gateway_factory=gateway.factory, cloud=cloud, standby=_standby(tmp_path))

    assert posted == 120
    assert [len(chunk) for chunk in cloud.posts] == [100, 20]


async def test_backfill_stops_at_an_older_cloud(tmp_path: Path) -> None:
    jobs = [_job(f"j{i}") for i in range(6)]
    gateway = _FakeGateway(
        pages=[{"jobs": jobs, "hasMore": False}],
        runs={job["id"]: [_run(job["id"], 1000 + n) for n in range(20)] for job in jobs},
    )
    cloud = _FakeCloud(accepted=False)

    assert await backfill_cron_runs(gateway_factory=gateway.factory, cloud=cloud, standby=_standby(tmp_path)) == 0
    assert len(cloud.posts) == 1


async def test_a_replaced_instance_backfills_nothing(tmp_path: Path) -> None:
    gateway = _FakeGateway(pages=[{"jobs": [_job("a")], "hasMore": False}], runs={"a": [_run("a", 1)]})
    cloud = _FakeCloud()
    standby = _standby(tmp_path)
    standby.request_standby()

    assert await backfill_cron_runs(gateway_factory=gateway.factory, cloud=cloud, standby=standby) == 0
    assert gateway.opened == 0
    assert cloud.posts == []


async def test_a_request_during_a_backfill_runs_exactly_one_more(tmp_path: Path) -> None:
    gate = asyncio.Event()
    started = 0

    class _SlowGateway(_FakeGateway):
        async def __aenter__(self) -> _FakeGateway:
            nonlocal started
            started += 1
            await gate.wait()
            return self

    gateway = _SlowGateway(pages=[{"jobs": [_job("a")], "hasMore": False}], runs={"a": [_run("a", 1)]})
    cloud = _FakeCloud()
    backfill = CronRunsBackfill(
        data_dir=tmp_path,
        standby=_standby(tmp_path),
        gateway_factory=gateway.factory,
        cloud_factory=lambda: cloud,
    )

    backfill.request()
    await asyncio.sleep(0)
    backfill.request()
    backfill.request()
    gate.set()
    await backfill.wait_idle()

    assert started == 2
    assert len(cloud.posts) == 2


async def test_requests_during_a_backfill_merge_and_a_full_pass_covers_named_jobs(tmp_path: Path) -> None:
    gate = asyncio.Event()

    class _SlowGateway(_FakeGateway):
        async def __aenter__(self) -> _FakeGateway:
            await gate.wait()
            return self

    gateway = _SlowGateway(
        pages=[{"jobs": [_job("a"), _job("b")], "hasMore": False}],
        runs={"a": [_run("a", 1)], "b": [_run("b", 2)]},
    )
    cloud = _FakeCloud()
    backfill = CronRunsBackfill(
        data_dir=tmp_path,
        standby=_standby(tmp_path),
        gateway_factory=gateway.factory,
        cloud_factory=lambda: cloud,
    )

    backfill.request(["a"])
    await asyncio.sleep(0)
    backfill.request(["b"])
    backfill.request(["a"])
    gate.set()
    await backfill.wait_idle()

    runs_calls = [params for method, params in gateway.calls if method == "cron.runs"]
    assert runs_calls == [{"id": "a", "limit": 3}, {"id": "a", "limit": 3}, {"id": "b", "limit": 3}]
    assert [method for method, _ in gateway.calls].count("cron.list") == 0


async def test_an_empty_targeted_request_does_nothing(tmp_path: Path) -> None:
    gateway = _FakeGateway(pages=[], runs={})
    backfill = CronRunsBackfill(
        data_dir=tmp_path, standby=_standby(tmp_path), gateway_factory=gateway.factory, cloud_factory=_FakeCloud
    )
    backfill.request([])
    await backfill.wait_idle()
    assert gateway.opened == 0


async def test_a_failing_backfill_is_logged_not_raised(tmp_path: Path) -> None:
    class _Down:
        async def __aenter__(self) -> Any:
            raise GatewayError("gateway unreachable")

        async def __aexit__(self, *_exc: object) -> None:
            return None

    backfill = CronRunsBackfill(
        data_dir=tmp_path,
        standby=_standby(tmp_path),
        gateway_factory=_Down,
        cloud_factory=_FakeCloud,
    )
    backfill.request()
    await backfill.wait_idle()
