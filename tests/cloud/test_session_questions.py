from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from sellerclaw_agent.cloud.cron_standby import CronStandby
from sellerclaw_agent.cloud.exceptions import CloudConnectionError
from sellerclaw_agent.cloud.session_questions import (
    MALFORMED_ERROR,
    OUTDATED_ERROR,
    STARTING_ERROR,
    TIMEOUT_ERROR,
    SessionQuestionDispatcher,
    usage_from_plugin,
)

pytestmark = pytest.mark.unit

REQUEST_ID = "33333333-3333-4333-8333-333333333333"
SESSION_KEY = "agent:supervisor:sellerclaw-ui:direct:44444444-4444-4444-8444-444444444444"


def _frame(**overrides: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "request_id": REQUEST_ID,
        "session_key": SESSION_KEY,
        "question": "[SellerClaw support] ...\n\nQuestion: Is anything stuck?",
        "timeout_ms": 45_000,
    }
    frame.update(overrides)
    return frame


class _FakePlugin:
    """The plugin's session-question route: answers from a script, records every body and budget."""

    def __init__(self, answer: dict[str, Any] | Exception | None = None) -> None:
        self.answer = answer if answer is not None else {"ok": True, "text": "Writing the About page."}
        self.calls: list[tuple[dict[str, Any], float]] = []

    async def post_session_question_json(self, body: dict[str, Any], *, answer_timeout_s: float) -> dict[str, Any]:
        self.calls.append((body, answer_timeout_s))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _FakeCloud:
    def __init__(self, *, accepted: bool = True, error: Exception | None = None) -> None:
        self.accepted = accepted
        self.error = error
        self.results: list[tuple[str, dict[str, Any]]] = []

    async def post_session_question_answer(self, *, request_id: str, result: dict[str, Any]) -> bool:
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
) -> SessionQuestionDispatcher:
    return SessionQuestionDispatcher(
        data_dir=tmp_path,
        standby=standby or CronStandby(tmp_path, plugin_factory=_NullStandbyPlugin),
        openclaw_gate=_gate(running, "running" if running else "stopped"),
        plugin_factory=lambda: plugin,
        cloud_factory=lambda: cloud,
    )


async def test_asks_the_plugin_and_reports_the_answer_with_its_usage(tmp_path: Path) -> None:
    plugin = _FakePlugin(
        {
            "ok": True,
            "text": "  Writing the About page.  ",
            "usage": {"inputTokens": 1200, "outputTokens": 80, "cacheReadTokens": 900, "totalTokens": 1280, "costUsd": 0.0123},
        }
    )
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    assert dispatcher.submit(_frame()) is not None
    await dispatcher.wait_idle()

    assert plugin.calls == [
        (
            {"sessionKey": SESSION_KEY, "question": "[SellerClaw support] ...\n\nQuestion: Is anything stuck?", "timeoutMs": 45_000},
            45.0,
        )
    ]
    assert cloud.results == [
        (
            REQUEST_ID,
            {
                "ok": True,
                "answer": "Writing the About page.",
                "error": None,
                "usage": {"input_tokens": 1200, "output_tokens": 80, "total_tokens": 1280, "cost_usd": 0.0123},
            },
        )
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param({}, None, id="nothing-measured"),
        pytest.param(
            {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12, "costUsd": 0},
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12, "cost_usd": None},
            id="zero-cost-is-unpriced",
        ),
        pytest.param(
            {"inputTokens": -1, "outputTokens": True, "totalTokens": "12", "costUsd": 0.5},
            {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost_usd": 0.5},
            id="junk-counts-dropped",
        ),
    ],
)
def test_usage_keeps_only_what_was_measured(raw: Any, expected: dict[str, Any] | None) -> None:
    assert usage_from_plugin(raw) == expected


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        pytest.param(
            {"ok": False, "error": "This chat's session hasn't run since the agent last restarted."},
            "This chat's session hasn't run since the agent last restarted.",
            id="plugin-explains",
        ),
        pytest.param({"ok": True, "text": "   "}, "The agent could not answer.", id="blank-answer"),
        pytest.param({"ok": False}, "The agent could not answer.", id="no-reason"),
        pytest.param(httpx.ConnectError("refused"), STARTING_ERROR, id="gateway-down"),
        pytest.param(httpx.ReadTimeout("slow"), TIMEOUT_ERROR, id="timeout"),
        pytest.param(
            httpx.HTTPStatusError(
                "404", request=httpx.Request("POST", "http://gw"), response=httpx.Response(404)
            ),
            OUTDATED_ERROR,
            id="plugin-without-the-route",
        ),
        pytest.param(
            httpx.HTTPStatusError(
                "500", request=httpx.Request("POST", "http://gw"), response=httpx.Response(500)
            ),
            "The agent refused the question (HTTP 500).",
            id="plugin-error",
        ),
        pytest.param(ValueError("route answered a non-object"), "The agent gave an unreadable answer: route answered a non-object", id="unreadable"),
    ],
)
async def test_reports_why_there_is_no_answer(
    tmp_path: Path, answer: dict[str, Any] | Exception, error: str
) -> None:
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=_FakePlugin(answer), cloud=cloud)

    dispatcher.submit(_frame())
    await dispatcher.wait_idle()

    assert cloud.results == [(REQUEST_ID, {"ok": False, "answer": None, "error": error, "usage": None})]


@pytest.mark.parametrize(
    ("timeout_ms", "budget_s"),
    [
        pytest.param(None, 45.0, id="missing"),
        pytest.param(1000, 5.0, id="too-short"),
        pytest.param(10_000_000, 120.0, id="too-long"),
        pytest.param("soon", 45.0, id="not-a-number"),
    ],
)
async def test_the_answer_budget_stays_within_bounds(tmp_path: Path, timeout_ms: Any, budget_s: float) -> None:
    plugin = _FakePlugin()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=_FakeCloud())

    dispatcher.submit(_frame(timeout_ms=timeout_ms))
    await dispatcher.wait_idle()

    [(body, budget)] = plugin.calls
    assert (body["timeoutMs"], budget) == (int(budget_s * 1000), budget_s)


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(_frame(session_key=""), id="no-session"),
        pytest.param(_frame(question="  "), id="no-question"),
    ],
)
async def test_a_malformed_frame_is_answered_without_asking(tmp_path: Path, frame: dict[str, Any]) -> None:
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    dispatcher.submit(frame)
    await dispatcher.wait_idle()

    assert plugin.calls == []
    assert cloud.results == [(REQUEST_ID, {"ok": False, "answer": None, "error": MALFORMED_ERROR, "usage": None})]


async def test_a_stopped_gateway_is_reported_without_asking(tmp_path: Path) -> None:
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud, running=False)

    dispatcher.submit(_frame())
    await dispatcher.wait_idle()

    assert plugin.calls == []
    assert cloud.results == [
        (REQUEST_ID, {"ok": False, "answer": None, "error": "The agent is not running (stopped).", "usage": None})
    ]


async def test_a_repeated_request_id_is_answered_once(tmp_path: Path) -> None:
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    assert dispatcher.submit(_frame()) is not None
    assert dispatcher.submit(_frame()) is None
    await dispatcher.wait_idle()

    assert len(plugin.calls) == 1
    assert len(cloud.results) == 1


async def test_a_frame_without_request_id_is_dropped(tmp_path: Path) -> None:
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud)

    assert dispatcher.submit(_frame(request_id="")) is None
    await dispatcher.wait_idle()

    assert plugin.calls == []
    assert cloud.results == []


async def test_a_replaced_instance_neither_answers_nor_reports(tmp_path: Path) -> None:
    standby = CronStandby(tmp_path, plugin_factory=_NullStandbyPlugin)
    standby.request_standby()
    plugin = _FakePlugin()
    cloud = _FakeCloud()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=cloud, standby=standby)

    dispatcher.submit(_frame())
    await dispatcher.wait_idle()

    assert plugin.calls == []
    assert cloud.results == []


async def test_a_cloud_that_cannot_take_the_answer_does_not_break_the_dispatcher(tmp_path: Path) -> None:
    plugin = _FakePlugin()
    dispatcher = _dispatcher(tmp_path, plugin=plugin, cloud=_FakeCloud(error=CloudConnectionError("down")))

    dispatcher.submit(_frame())
    dispatcher.submit(_frame(request_id="55555555-5555-4555-8555-555555555555"))
    await dispatcher.wait_idle()

    assert len(plugin.calls) == 2
