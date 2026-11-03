from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from sellerclaw_agent.cloud.cron_commands import CronCommandDispatcher, command_result
from sellerclaw_agent.cloud.cron_standby import CronStandby
from sellerclaw_agent.cloud.exceptions import CloudConnectionError

pytestmark = pytest.mark.unit

REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _frame(**overrides: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "request_id": REQUEST_ID,
        "user_id": "22222222-2222-4222-8222-222222222222",
        "action": "update",
        "job_id": "job-1",
        "job": None,
        "patch": {"enabled": False},
    }
    frame.update(overrides)
    return frame


class _FakePlugin:
    """The plugin's cron command route: answers from a script, records every frame."""

    def __init__(self, answer: dict[str, Any] | Exception | None = None) -> None:
        self.answer = answer if answer is not None else command_result(ok=True, store_epoch="epoch-1")
        self.frames: list[dict[str, Any]] = []
        self.release: asyncio.Event | None = None

    async def post_cron_command_json(self, body: dict[str, Any]) -> dict[str, Any]:
        self.frames.append(body)
        if self.release is not None:
            await self.release.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _FakeCloud:
    def __init__(self, *, accepted: bool = True, error: Exception | None = None) -> None:
        self.accepted = accepted
        self.error = error
        self.results: list[tuple[str, dict[str, Any]]] = []

    async def post_cron_command_result(self, *, request_id: str, result: dict[str, Any]) -> bool:
        if self.error is not None:
            raise self.error
        self.results.append((request_id, result))
        return self.accepted


class _NullStandbyPlugin:
    async def post_cron_standby_json(self, *, standby: bool) -> tuple[bool, dict[str, Any]]:
        return True, {}


def _gate(running: bool, status: str = "running"):
    async def gate() -> tuple[bool, str, str | None]:
        return running, status, None

    return gate


def _dispatcher(
    tmp_path: Path,
    *,
    plugin: _FakePlugin,
    cloud: _FakeCloud,
    running: bool = True,
    standby: CronStandby | None = None,
) -> CronCommandDispatcher:
    return CronCommandDispatcher(
        data_dir=tmp_path,
        standby=standby or CronStandby(tmp_path, plugin_factory=_NullStandbyPlugin),
        openclaw_gate=_gate(running, "running" if running else "stopped"),
        plugin_factory=lambda: plugin,
        cloud_factory=lambda: cloud,
    )


async def test_runs_the_command_in_the_plugin_and_reports_its_outcome(tmp_path: Path) -> None:
    job = {"id": "job-1", "enabled": False, "declarationKey": "sellerclaw:job:1"}
    plugin = _FakePlugin(command_result(ok=True, store_epoch="epoch-1", job=job))
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    task = dispatcher.submit(_frame())
    assert task is not None
    await dispatcher.wait_idle()

    # user_id stays on the cloud side; the plugin gets the contract fields only.
    assert plugin.frames == [
        {"request_id": REQUEST_ID, "action": "update", "job_id": "job-1", "job": None, "patch": {"enabled": False}}
    ]
    assert cloud.results == [
        (REQUEST_ID, {"ok": True, "error": None, "store_epoch": "epoch-1", "job": job, "run_id": None})
    ]


async def test_a_repeated_request_id_runs_once(tmp_path: Path) -> None:
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    assert dispatcher.submit(_frame()) is not None
    assert dispatcher.submit(_frame()) is None
    await dispatcher.wait_idle()

    assert len(plugin.frames) == 1
    assert len(cloud.results) == 1


async def test_a_frame_without_request_id_is_dropped(tmp_path: Path) -> None:
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    assert dispatcher.submit(_frame(request_id="")) is None
    await dispatcher.wait_idle()
    assert plugin.frames == []
    assert cloud.results == []


@pytest.mark.parametrize(
    ("running", "action", "error"),
    [
        pytest.param(False, "run", "The assistant is not running (stopped).", id="openclaw-stopped"),
        pytest.param(True, "explode", "unknown action: explode", id="unknown-action"),
        pytest.param(True, "", "unknown action: (none)", id="no-action"),
    ],
)
async def test_refused_without_reaching_the_plugin(
    tmp_path: Path, running: bool, action: str, error: str
) -> None:
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud, running=running)

    dispatcher.submit(_frame(action=action))
    await dispatcher.wait_idle()

    assert plugin.frames == []
    assert cloud.results == [
        (REQUEST_ID, {"ok": False, "error": error, "store_epoch": None, "job": None, "run_id": None})
    ]


_REQUEST = httpx.Request("POST", "http://gw.test/api/channels/sellerclaw-ui/cron/command")


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        pytest.param(
            httpx.ConnectError("refused", request=_REQUEST),
            "The assistant is starting; try again in a minute.",
            id="gateway-down",
        ),
        pytest.param(
            httpx.ReadTimeout("slow", request=_REQUEST),
            "The assistant did not answer in time.",
            id="timeout",
        ),
        pytest.param(
            httpx.HTTPStatusError("bad", request=_REQUEST, response=httpx.Response(400, request=_REQUEST)),
            "The assistant refused the command (HTTP 400).",
            id="route-refused",
        ),
        pytest.param(
            ValueError("cron command route answered a non-object"),
            "The assistant gave an unreadable answer: cron command route answered a non-object",
            id="junk-answer",
        ),
    ],
)
async def test_plugin_failures_become_a_failed_outcome(
    tmp_path: Path, failure: Exception, error: str
) -> None:
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=_FakePlugin(failure), cloud=cloud)

    dispatcher.submit(_frame())
    await dispatcher.wait_idle()

    assert cloud.results == [
        (REQUEST_ID, {"ok": False, "error": error, "store_epoch": None, "job": None, "run_id": None})
    ]


async def test_a_plugin_answer_is_narrowed_to_the_contract(tmp_path: Path) -> None:
    plugin = _FakePlugin({"ok": "yes", "error": "", "store_epoch": 5, "job": "j", "run_id": "", "extra": 1})
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    dispatcher.submit(_frame())
    await dispatcher.wait_idle()

    assert cloud.results == [
        (REQUEST_ID, {"ok": False, "error": None, "store_epoch": None, "job": None, "run_id": None})
    ]


async def test_a_replaced_instance_neither_runs_nor_reports(tmp_path: Path) -> None:
    standby = CronStandby(tmp_path, plugin_factory=_NullStandbyPlugin)
    standby.request_standby()
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud, standby=standby)

    dispatcher.submit(_frame())
    await dispatcher.wait_idle()

    assert plugin.frames == []
    assert cloud.results == []


async def test_commands_run_one_at_a_time_in_arrival_order(tmp_path: Path) -> None:
    plugin = _FakePlugin()
    plugin.release = asyncio.Event()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    dispatcher.submit(_frame(request_id="a", action="add", job={"name": "x"}, job_id=None, patch=None))
    dispatcher.submit(_frame(request_id="b", action="run", job_id="job-1", patch=None))
    await asyncio.sleep(0.01)
    assert [f["request_id"] for f in plugin.frames] == ["a"], "the second waits for the first"

    plugin.release.set()
    await dispatcher.wait_idle()
    assert [f["request_id"] for f in plugin.frames] == ["a", "b"]
    assert [request_id for request_id, _ in cloud.results] == ["a", "b"]


@pytest.mark.parametrize(
    "cloud",
    [
        pytest.param(_FakeCloud(accepted=False), id="older-cloud"),
        pytest.param(_FakeCloud(error=CloudConnectionError("down")), id="cloud-down"),
    ],
)
async def test_an_unreported_outcome_does_not_break_the_dispatcher(tmp_path: Path, cloud: _FakeCloud) -> None:
    plugin = _FakePlugin()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    dispatcher.submit(_frame(request_id="a"))
    await dispatcher.wait_idle()
    dispatcher.submit(_frame(request_id="b"))
    await dispatcher.wait_idle()

    assert [f["request_id"] for f in plugin.frames] == ["a", "b"]
