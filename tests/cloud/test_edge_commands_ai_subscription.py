"""Edge commands of protocol 10: ``ai_subscription_disconnect`` and START after a post-connect restore."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sellerclaw_agent.server.edge_commands import _execute_remote_command

pytestmark = pytest.mark.unit


class _RecordingDisconnect:
    """Stands in for ``ai_subscription_routes.disconnect``; records each call in ``events``."""

    def __init__(self, events: list[str], *, error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.calls: list[tuple[Path, Any]] = []

    def __call__(self, data_dir: Path, manager: Any) -> Any:
        self.calls.append((data_dir, manager))
        self.events.append("disconnect")
        if self.error is not None:
            raise self.error
        return MagicMock(applied=True, error=None)


async def _drain(executor: ThreadPoolExecutor) -> None:
    """Wait until everything queued on the single-worker executor so far has run."""
    await asyncio.get_running_loop().run_in_executor(executor, lambda: None)


async def test_switch_to_credits_is_acked_before_it_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gateway restart can outlast the cloud's wait for an answer: ack first, then do it."""
    events: list[str] = []
    disconnect = _RecordingDisconnect(events)
    monkeypatch.setattr("sellerclaw_agent.server.ai_subscription_routes.disconnect", disconnect)
    mgr = MagicMock()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_ai_sub")
    busy = threading.Event()
    # The supervisor executor is busy (a START still running): the switch queues behind it.
    executor.submit(lambda: (busy.wait(5), events.append("start finished")))
    try:
        outcome, err = await _execute_remote_command(
            loop=asyncio.get_running_loop(),
            executor=executor,
            cmd_type="ai_subscription_disconnect",
            client=MagicMock(),
            data_dir=tmp_path,
            container_mgr=mgr,
        )
        assert (outcome, err) == ("completed", None)
        assert events == [], "acked before anything ran"

        busy.set()
        await _drain(executor)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert events == ["start finished", "disconnect"], "never beside a START / RESTART"
    assert disconnect.calls == [(tmp_path, mgr)]


async def test_a_failing_switch_to_credits_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    disconnect = _RecordingDisconnect(events, error=OSError("disk full"))
    monkeypatch.setattr("sellerclaw_agent.server.ai_subscription_routes.disconnect", disconnect)
    warnings: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "sellerclaw_agent.server.edge_commands._log.warning",
        lambda event, **kw: warnings.append({"event": event, **kw}),
    )
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_ai_sub_err")
    try:
        outcome, _err = await _execute_remote_command(
            loop=asyncio.get_running_loop(),
            executor=executor,
            cmd_type="ai_subscription_disconnect",
            client=MagicMock(),
            data_dir=tmp_path,
            container_mgr=MagicMock(),
        )
        await _drain(executor)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    assert events == ["disconnect"]
    assert warnings == [{"event": "ai_subscription_disconnect_failed", "error": "disk full"}]


class _FakeRestore:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def wait(self, timeout_s: float = 40.0) -> bool:
        self.events.append("restore waited")
        return True


@pytest.mark.parametrize("cmd_type", ["start", "restart"])
async def test_start_waits_for_the_post_connect_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cmd_type: str
) -> None:
    """Sign-ins restored after the first connect must be on disk before OpenClaw reads them."""
    events: list[str] = []
    monkeypatch.setattr("sellerclaw_agent.server.edge_commands.get_post_connect_restore", lambda: _FakeRestore(events))
    monkeypatch.setattr(
        "sellerclaw_agent.server.edge_commands.bundle_manifest_from_mapping", lambda _m: MagicMock(name="manifest")
    )
    client = MagicMock()
    client.fetch_edge_manifest = AsyncMock(return_value={"user_id": "00000000-0000-0000-0000-000000000001"})
    mgr = MagicMock()

    def _started(_manifest: Any) -> tuple[str, None]:
        events.append(f"{cmd_type} ran")
        return "completed", None

    getattr(mgr, cmd_type).side_effect = _started
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_start_restore")
    try:
        outcome, err = await _execute_remote_command(
            loop=asyncio.get_running_loop(),
            executor=executor,
            cmd_type=cmd_type,
            client=client,
            data_dir=tmp_path,
            container_mgr=mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert (outcome, err) == ("completed", None)
    assert events == ["restore waited", f"{cmd_type} ran"]


async def test_start_without_a_restore_starts_right_away(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sellerclaw_agent.server.edge_commands.get_post_connect_restore", lambda: None)
    monkeypatch.setattr(
        "sellerclaw_agent.server.edge_commands.bundle_manifest_from_mapping", lambda _m: MagicMock(name="manifest")
    )
    client = MagicMock()
    client.fetch_edge_manifest = AsyncMock(return_value={"user_id": "00000000-0000-0000-0000-000000000001"})
    mgr = MagicMock()
    mgr.start = MagicMock(return_value=("completed", None))
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_start_plain")
    try:
        outcome, _err = await _execute_remote_command(
            loop=asyncio.get_running_loop(),
            executor=executor,
            cmd_type="start",
            client=client,
            data_dir=tmp_path,
            container_mgr=mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    mgr.start.assert_called_once()
