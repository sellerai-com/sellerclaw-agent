from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from sellerclaw_agent.cloud import cron_standby as cs
from sellerclaw_agent.cloud.cron_standby import CronStandby, get_cron_standby

pytestmark = pytest.mark.unit


class _FakePlugin:
    """The plugin's standby route: answers from a script, records every requested state."""

    def __init__(self, *answers: tuple[bool, dict[str, Any]] | Exception) -> None:
        self.answers = list(answers) or [(True, {"ok": True})]
        self.requested: list[bool] = []

    async def post_cron_standby_json(self, *, standby: bool) -> tuple[bool, dict[str, Any]]:
        self.requested.append(standby)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_standby_is_persisted_in_the_data_dir(tmp_path: Path) -> None:
    standby = CronStandby(tmp_path, plugin_factory=_FakePlugin)
    assert standby.is_standby() is False

    standby.request_standby()
    assert (tmp_path / "cron_standby.json").exists()
    # A restarted agent server keeps the wish.
    assert CronStandby(tmp_path, plugin_factory=_FakePlugin).is_standby() is True

    standby.request_active()
    assert not (tmp_path / "cron_standby.json").exists()
    assert CronStandby(tmp_path, plugin_factory=_FakePlugin).is_standby() is False


async def test_reconcile_delivers_each_wish_once(tmp_path: Path) -> None:
    plugin = _FakePlugin()
    standby = CronStandby(tmp_path, plugin_factory=lambda: plugin)

    # A fresh process does not know the plugin's state: it says "active" once.
    assert await standby.reconcile() is True
    assert await standby.reconcile() is True
    assert plugin.requested == [False]

    standby.request_standby()
    standby.request_standby()  # repeated refusals change nothing
    assert await standby.reconcile() is True
    assert await standby.reconcile() is True
    assert plugin.requested == [False, True]

    standby.request_active()
    assert await standby.reconcile() is True
    assert plugin.requested == [False, True, False]


_REQUEST = httpx.Request("POST", "http://gw.test/api/channels/sellerclaw-ui/cron/standby")


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.ConnectError("refused", request=_REQUEST), id="gateway-down"),
        pytest.param(httpx.ReadTimeout("slow", request=_REQUEST), id="timeout"),
        pytest.param((False, {"ok": False, "error": "scheduler not ready"}), id="not-held"),
    ],
)
async def test_an_undelivered_wish_is_retried_on_the_next_reconcile(tmp_path: Path, failure: Any) -> None:
    plugin = _FakePlugin(failure, (True, {"ok": True}))
    standby = CronStandby(tmp_path, plugin_factory=lambda: plugin)
    standby.request_standby()

    assert await standby.reconcile() is False
    assert await standby.reconcile() is True
    assert plugin.requested == [True, True]


async def test_a_hung_plugin_is_given_up_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Hung:
        async def post_cron_standby_json(self, *, standby: bool) -> tuple[bool, dict[str, Any]]:
            await asyncio.sleep(10)
            return True, {}

    monkeypatch.setattr(cs, "RECONCILE_TIMEOUT_S", 0.01)
    standby = CronStandby(tmp_path, plugin_factory=_Hung)
    assert await standby.reconcile() is False


def test_one_standby_per_data_dir(tmp_path: Path) -> None:
    assert get_cron_standby(tmp_path) is get_cron_standby(tmp_path)
    assert get_cron_standby(tmp_path / "other") is not get_cron_standby(tmp_path)
