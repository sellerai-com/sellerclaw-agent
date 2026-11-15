"""START / RESTART / UPDATE_MANIFEST fetch the manifest first; a network blip is retried.

The cloud sends START once, right after this computer connects. Live on staging (14.11.2026) one
fetch timed out on a flaky network, the START failed with an empty reason and the agent stayed
stopped until the owner pressed Start.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sellerclaw_agent.cloud.exceptions import CloudAuthError, CloudConnectionError
from sellerclaw_agent.server import edge_commands
from sellerclaw_agent.server.edge_commands import _execute_remote_command

pytestmark = pytest.mark.unit

_MAPPING: dict[str, Any] = {"user_id": "00000000-0000-0000-0000-000000000001"}


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(edge_commands.asyncio, "sleep", _instant)
    monkeypatch.setattr(edge_commands, "bundle_manifest_from_mapping", lambda _mapping: MagicMock(name="manifest"))
    monkeypatch.setattr(edge_commands, "get_post_connect_restore", lambda: None)


async def _run(tmp_path: Path, cmd_type: str, fetch: AsyncMock) -> tuple[tuple[str, str | None], MagicMock]:
    client = MagicMock()
    client.fetch_edge_manifest = fetch
    container_mgr = MagicMock()
    for method in ("start", "restart", "update_manifest"):
        getattr(container_mgr, method).return_value = ("completed", None)
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_manifest_retry")
    try:
        result = await _execute_remote_command(
            loop=asyncio.get_running_loop(),
            executor=executor,
            cmd_type=cmd_type,
            client=client,
            data_dir=tmp_path,
            container_mgr=container_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result, container_mgr


@pytest.mark.parametrize("cmd_type", ["start", "restart", "update_manifest"])
@pytest.mark.parametrize(
    "answers",
    [
        pytest.param([_MAPPING], id="first-try"),
        pytest.param([CloudConnectionError(""), _MAPPING], id="after-a-timeout"),
        pytest.param([CloudConnectionError("reset"), CloudConnectionError(""), _MAPPING], id="on-the-last-try"),
    ],
)
async def test_a_blip_does_not_stop_the_command(tmp_path: Path, cmd_type: str, answers: list[Any]) -> None:
    fetch = AsyncMock(side_effect=answers)

    (outcome, error), container_mgr = await _run(tmp_path, cmd_type, fetch)

    assert (outcome, error) == ("completed", None)
    assert fetch.await_count == len(answers)
    getattr(container_mgr, cmd_type).assert_called_once()
    # Every try is short, so all of them still answer the cloud within its minute.
    assert all(call.kwargs["timeout"].connect == 5.0 for call in fetch.await_args_list)


@pytest.mark.parametrize(
    ("answers", "tries", "reason"),
    [
        pytest.param(
            [CloudConnectionError("")] * 3,
            3,
            "Could not reach SellerClaw to load the agent's settings.",
            id="cloud-unreachable-says-so",
        ),
        pytest.param([CloudConnectionError("502 bad gateway")] * 3, 3, "502 bad gateway", id="keeps-the-cloud-reason"),
        pytest.param([CloudAuthError("Invalid bearer token", status_code=401)], 1, "Invalid bearer token", id="revoked-token-not-retried"),
    ],
)
async def test_a_manifest_that_never_comes_fails_with_a_reason(
    tmp_path: Path, answers: list[Exception], tries: int, reason: str
) -> None:
    fetch = AsyncMock(side_effect=answers)

    (outcome, error), container_mgr = await _run(tmp_path, "start", fetch)

    assert (outcome, error) == ("failed", reason)
    assert fetch.await_count == tries
    container_mgr.start.assert_not_called()
