from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sellerclaw_agent.server.cron_routes as cron_routes
from sellerclaw_agent.server.secrets_store import reset_secrets_cache
from sellerclaw_agent.server.storage import ManifestStorage

pytestmark = pytest.mark.unit


class _FakeBackfill:
    def __init__(self) -> None:
        self.requests: list[list[str] | None] = []

    def request(self, job_ids: list[str] | None = None) -> None:
        self.requests.append(job_ids)


@pytest.fixture()
def backfill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeBackfill:
    reset_secrets_cache()
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    ManifestStorage(tmp_path).save({"user_id": "11111111-1111-4111-8111-111111111111"})
    (tmp_path / "secrets.json").write_text(
        json.dumps({"local_api_key": "local", "gateway_token": "gw", "hooks_token": "hooks-secret"}),
        encoding="utf-8",
    )
    fake = _FakeBackfill()
    monkeypatch.setattr(cron_routes, "get_cron_runs_backfill", lambda: fake)
    return fake


@pytest.fixture()
def client(backfill: _FakeBackfill) -> TestClient:
    app = FastAPI()
    app.include_router(cron_routes.router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param({}, None, id="every-job"),
        pytest.param(None, None, id="no-body"),
        pytest.param({"job_ids": ["j1", "j2"]}, ["j1", "j2"], id="named-jobs"),
    ],
)
def test_backfill_request_is_accepted_and_runs_in_the_background(
    client: TestClient, backfill: _FakeBackfill, body: dict | None, expected: list[str] | None
) -> None:
    response = client.post(
        "/internal/openclaw/cron/backfill-runs", headers={"Authorization": "Bearer hooks-secret"}, json=body
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert backfill.requests == [expected]


def test_backfill_refuses_too_many_job_ids(client: TestClient, backfill: _FakeBackfill) -> None:
    response = client.post(
        "/internal/openclaw/cron/backfill-runs",
        headers={"Authorization": "Bearer hooks-secret"},
        json={"job_ids": [f"j{i}" for i in range(51)]},
    )
    assert response.status_code == 422
    assert backfill.requests == []


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer wrong"}, id="wrong-token"),
    ],
)
def test_backfill_needs_the_hooks_token(client: TestClient, backfill: _FakeBackfill, headers: dict[str, str]) -> None:
    response = client.post("/internal/openclaw/cron/backfill-runs", headers=headers, json={})
    assert response.status_code == 401
    assert backfill.requests == []
