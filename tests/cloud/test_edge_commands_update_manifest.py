"""Edge handler for the ``update_manifest`` remote command.

Verifies that the cloud → edge hot-reload path lands on
``SupervisorContainerManager.update_manifest`` (no supervisorctl restart) and
surfaces failures from the fetch / parse / write stages as command failures
rather than crashing the executor loop.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sellerclaw_agent.server.edge_commands import _execute_remote_command

pytestmark = pytest.mark.unit


def _minimal_manifest_mapping() -> dict[str, object]:
    """Smallest valid mapping the bundle parser accepts in tests.

    The handler only needs ``bundle_manifest_from_mapping`` to succeed — the
    actual rebuild is mocked via ``container_mgr.update_manifest``.
    """
    # bundle_manifest_from_mapping reads minimally — supply only required top-
    # level keys. The fixture-managed fuller version lives in unit/server but
    # we sidestep it here by mocking the manifest parser.
    return {"user_id": "00000000-0000-0000-0000-000000000001"}


async def test_update_manifest_routes_to_container_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: handler calls ``container_mgr.update_manifest`` and forwards its result."""
    mapping = _minimal_manifest_mapping()
    mock_client = MagicMock()
    mock_client.fetch_edge_manifest = AsyncMock(return_value=mapping)
    mock_mgr = MagicMock()
    mock_mgr.update_manifest = MagicMock(return_value=("completed", None))

    parsed = MagicMock(name="GenericManifest")
    monkeypatch.setattr(
        "sellerclaw_agent.server.edge_commands.bundle_manifest_from_mapping",
        lambda _: parsed,
    )

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_um")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="update_manifest",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    assert err is None
    mock_client.fetch_edge_manifest.assert_awaited_once()
    mock_mgr.update_manifest.assert_called_once_with(parsed)
    # The whole point: no restart was invoked on the container manager.
    assert not mock_mgr.restart.called
    assert not mock_mgr.start.called


async def test_update_manifest_surfaces_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If fetching the manifest from cloud fails, the command is reported as failed.

    Without this guard a transient cloud outage would bubble an unhandled
    exception up through the executor loop and kill the ping worker.
    """
    mock_client = MagicMock()
    mock_client.fetch_edge_manifest = AsyncMock(side_effect=RuntimeError("network down"))
    mock_mgr = MagicMock()

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_um_err")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="update_manifest",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "failed"
    assert err is not None and "network down" in err
    mock_mgr.update_manifest.assert_not_called()


async def test_update_manifest_propagates_proxy_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy-change rejection from the container manager flows back as ``failed``.

    Cloud's classifier should never send a proxy change down this path, but if
    it does the edge refuses loudly rather than silently dropping the new value.
    """
    mock_client = MagicMock()
    mock_client.fetch_edge_manifest = AsyncMock(return_value=_minimal_manifest_mapping())
    mock_mgr = MagicMock()
    mock_mgr.update_manifest = MagicMock(
        return_value=("failed", "proxy change requires a full restart"),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.edge_commands.bundle_manifest_from_mapping",
        lambda _: MagicMock(name="GenericManifest"),
    )

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_um_proxy")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="update_manifest",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "failed"
    assert err == "proxy change requires a full restart"
