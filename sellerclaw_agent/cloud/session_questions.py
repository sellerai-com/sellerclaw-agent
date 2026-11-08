"""``session_question`` frames from the cloud: a staff side question to one of the owner's sessions.

SellerClaw staff ask the owner's agent about one chat from the admin panel. The frame —
``{request_id, session_key, question, timeout_ms}`` — goes to the ``sellerclaw-ui`` plugin, which
answers it from a read-only copy of that session (OpenClaw ``forkSession``): the session's model
sees the conversation and what the agent did, cannot use tools, and writes nothing back, so the
owner and the chat never see it. This module posts the answer — or why there is none — to
``POST /agent/session-questions/{request_id}``.

Each frame runs as its own task, so the stream keeps flowing while the model thinks; questions
still run one at a time (a fork takes a subagent slot). A repeated ``request_id`` is dropped. A
replaced instance (cron standby) neither answers nor reports: the active one holds the live session.
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

DEFAULT_TIMEOUT_MS: Final[int] = 45_000
MIN_TIMEOUT_MS: Final[int] = 5_000
MAX_TIMEOUT_MS: Final[int] = 120_000
_MAX_ANSWER_CHARS: Final[int] = 20_000
_MAX_ERROR_CHARS: Final[int] = 2000

MALFORMED_ERROR: Final[str] = "The question did not reach the agent intact."
STARTING_ERROR: Final[str] = "The agent is starting; try again in a minute."
TIMEOUT_ERROR: Final[str] = "The agent didn't answer in time. Try again in a minute."
OUTDATED_ERROR: Final[str] = "The agent needs an update to answer questions about a chat."


class SessionQuestionPlugin(Protocol):
    async def post_session_question_json(self, body: dict[str, Any], *, answer_timeout_s: float) -> dict[str, Any]: ...


class SessionQuestionCloud(Protocol):
    async def post_session_question_answer(self, *, request_id: str, result: dict[str, Any]) -> bool: ...


def _default_plugin_factory(data_dir: Path) -> Callable[[], SessionQuestionPlugin]:
    def _factory() -> SessionQuestionPlugin:
        # No shared HTTP client: a question task may outlive the stream connection that delivered it.
        secrets = get_secrets(data_dir)
        return LocalOpenClawForwarder(
            base_url=openclaw_gateway_base_url(),
            hooks_token=secrets.hooks_token,
            gateway_token=secrets.gateway_token,
        )

    return _factory


def _default_cloud_factory(data_dir: Path) -> Callable[[], SessionQuestionCloud]:
    def _factory() -> SessionQuestionCloud:
        return SellerClawConnectionClient(credentials_storage=CredentialsStorage(data_dir))

    return _factory


def answer_result(
    *,
    ok: bool,
    answer: str | None = None,
    error: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The body of ``POST /agent/session-questions/{request_id}``."""
    return {
        "ok": ok,
        "answer": answer[:_MAX_ANSWER_CHARS] if answer else None,
        "error": error[:_MAX_ERROR_CHARS] if error else None,
        "usage": usage,
    }


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return int(value)


def usage_from_plugin(raw: Any) -> dict[str, Any] | None:
    """OpenClaw's fork usage as the cloud reads it; an unmeasured part stays ``None``.

    A cost of zero is OpenClaw saying it has no price for the model, never that the answer was
    free — so it is dropped rather than shown as ``$0``.
    """
    if not isinstance(raw, dict):
        return None
    cost = raw.get("costUsd")
    usage = {
        "input_tokens": _count(raw.get("inputTokens")),
        "output_tokens": _count(raw.get("outputTokens")),
        "total_tokens": _count(raw.get("totalTokens")),
        "cost_usd": float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) and cost > 0 else None,
    }
    return usage if any(value is not None for value in usage.values()) else None


def _result_from_plugin(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only the contract's fields from the plugin's answer, typed as the cloud expects."""
    text = payload.get("text")
    error = payload.get("error")
    usage = usage_from_plugin(payload.get("usage"))
    if payload.get("ok") is True and isinstance(text, str) and text.strip():
        return answer_result(ok=True, answer=text.strip(), usage=usage)
    return answer_result(
        ok=False,
        error=error if isinstance(error, str) and error else "The agent could not answer.",
        usage=usage,
    )


def _timeout_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return DEFAULT_TIMEOUT_MS
    return min(MAX_TIMEOUT_MS, max(MIN_TIMEOUT_MS, int(value)))


class SessionQuestionDispatcher:
    """Answers ``session_question`` frames off the stream loop, one at a time, each request id once."""

    def __init__(
        self,
        *,
        data_dir: Path,
        standby: CronStandby,
        openclaw_gate: OpenClawGate | None = None,
        plugin_factory: Callable[[], SessionQuestionPlugin] | None = None,
        cloud_factory: Callable[[], SessionQuestionCloud] | None = None,
        remember: int = 256,
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
        """Start answering one frame in the background; ``None`` when it is dropped right away."""
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            _log.warning("session_question_missing_request_id")
            return None
        if request_id in self._seen:
            self._seen.move_to_end(request_id)
            _log.info("session_question_duplicate_dropped", request_id=request_id)
            return None
        self._seen[request_id] = None
        while len(self._seen) > self._remember:
            self._seen.popitem(last=False)
        task = asyncio.create_task(self._handle(request_id, payload), name=f"session_question:{request_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _handle(self, request_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            if self._standby.is_standby():
                _log.info("session_question_ignored_on_standby", request_id=request_id)
                return
            try:
                result = await self._execute(payload)
            except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the dispatcher
                _log.exception("session_question_failed_unexpectedly", request_id=request_id)
                result = answer_result(ok=False, error=f"The agent could not answer: {exc}")
            usage = result["usage"] or {}
            _log.info(
                "session_question_done",
                request_id=request_id,
                ok=result["ok"],
                error=result["error"],
                total_tokens=usage.get("total_tokens"),
                cost_usd=usage.get("cost_usd"),
            )
            await self._report(request_id, result)

    async def _execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_key = str(payload.get("session_key") or "").strip()
        question = str(payload.get("question") or "").strip()
        if not session_key or not question:
            return answer_result(ok=False, error=MALFORMED_ERROR)
        if self._gate is not None:
            running, status, _err = await self._gate()
            if not running:
                return answer_result(ok=False, error=f"The agent is not running ({status}).")
        timeout_ms = _timeout_ms(payload.get("timeout_ms"))
        body = {"sessionKey": session_key, "question": question, "timeoutMs": timeout_ms}
        try:
            answer = await self._plugin_factory().post_session_question_json(body, answer_timeout_s=timeout_ms / 1000)
        except httpx.ConnectError:
            return answer_result(ok=False, error=STARTING_ERROR)
        except httpx.TimeoutException:
            return answer_result(ok=False, error=TIMEOUT_ERROR)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return answer_result(ok=False, error=OUTDATED_ERROR)
            return answer_result(ok=False, error=f"The agent refused the question (HTTP {exc.response.status_code}).")
        except ValueError as exc:
            return answer_result(ok=False, error=f"The agent gave an unreadable answer: {exc}")
        return _result_from_plugin(answer)

    async def _report(self, request_id: str, result: dict[str, Any]) -> None:
        if self._standby.is_standby():
            return
        try:
            accepted = await self._cloud_factory().post_session_question_answer(request_id=request_id, result=result)
        except (CloudAuthError, CloudConnectionError) as exc:
            _log.warning("session_question_answer_not_reported", request_id=request_id, error=str(exc)[:300])
            return
        if not accepted:
            _log.debug("session_question_answer_endpoint_missing", request_id=request_id)

    async def wait_idle(self) -> None:
        """Wait for every submitted frame to finish (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
