"""A cloud job is created over the gateway's ``cron.add`` RPC: only that RPC records the job's inference
account, without which OpenClaw refuses every run on a machine holding a stored account."""

from __future__ import annotations

from typing import Any

import pytest
from openclaw_diagnostics.gateway import GatewayError, GatewayUnreachableError

from sellerclaw_agent.cloud.cron_add import add_cron_job

pytestmark = pytest.mark.unit

_JOB: dict[str, Any] = {
    "name": "Morning summary",
    "description": "",
    "enabled": True,
    "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"},
    "sessionTarget": "isolated",
    "wakeMode": "now",
    "payload": {"kind": "agentTurn", "message": "Summarize yesterday's orders."},
    "delivery": {"mode": "none"},
    "agentId": "supervisor",
    "declarationKey": "sellerclaw:job:7a1c0b52-6c51-4a49-9d0b-3c2b8f0e4d11",
}
_STORED: dict[str, Any] = {**_JOB, "id": "345bb59c-0a1e-4752-bed1-88b5440ca564", "createdAtMs": 1795106000000}


class _FakeAddGateway:
    """``cron.add`` answers from a script (or fails); every call is recorded."""

    def __init__(self, *, outcome: Any = None, error: Exception | None = None, unreachable: bool = False) -> None:
        self.outcome = outcome if outcome is not None else {"created": True, "deliveryPreview": None, "job": _STORED}
        self.error = error
        self.unreachable = unreachable
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    def factory(self) -> _FakeAddGateway:
        return self

    async def __aenter__(self) -> _FakeAddGateway:
        if self.unreachable:
            raise GatewayUnreachableError("gateway unreachable at ws://127.0.0.1:7789")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        self.calls.append((method, params or {}, timeout))
        if self.error is not None:
            raise self.error
        return self.outcome


async def test_the_job_goes_over_cron_add_and_comes_back_as_stored() -> None:
    gateway = _FakeAddGateway()

    result = await add_cron_job(dict(_JOB), gateway_factory=gateway.factory)

    assert result == {"ok": True, "job": _STORED, "error": None}
    assert gateway.calls == [("cron.add", _JOB, 30.0)]


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(None, id="no-key"),
        pytest.param("memory-core:memory-dreaming-promotion", id="system-key"),
        pytest.param("", id="empty-key"),
    ],
)
async def test_a_job_the_cloud_does_not_own_is_refused_without_reaching_the_gateway(key: str | None) -> None:
    gateway = _FakeAddGateway()
    job = {k: v for k, v in _JOB.items() if k != "declarationKey"}
    if key is not None:
        job["declarationKey"] = key

    result = await add_cron_job(job, gateway_factory=gateway.factory)

    assert result == {
        "ok": False,
        "job": None,
        "error": "add needs a declarationKey starting with sellerclaw:job:",
    }
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("gateway", "error"),
    [
        pytest.param(
            _FakeAddGateway(
                error=GatewayError(
                    "cron.add failed: INVALID_REQUEST: Automation requires a stored inference account or a key "
                    "in the environment or provider config. Select an account with model@profile."
                )
            ),
            "cron.add failed: INVALID_REQUEST: Automation requires a stored inference account or a key "
            "in the environment or provider config. Select an account with model@profile.",
            id="gateway-refuses",
        ),
        pytest.param(
            _FakeAddGateway(unreachable=True),
            "The assistant is starting; try again in a minute.",
            id="gateway-not-up",
        ),
        pytest.param(
            _FakeAddGateway(outcome={"created": True}),
            "The gateway gave an unreadable answer: {'created': True}",
            id="no-job-in-answer",
        ),
    ],
)
async def test_a_failed_add_is_reported_with_its_reason(gateway: _FakeAddGateway, error: str) -> None:
    result = await add_cron_job(dict(_JOB), gateway_factory=gateway.factory)

    assert result == {"ok": False, "job": None, "error": error}
