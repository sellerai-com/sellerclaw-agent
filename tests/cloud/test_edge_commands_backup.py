from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sellerclaw_agent.server.edge_commands import _execute_remote_command

pytestmark = pytest.mark.unit


async def test_stop_uploads_state_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    (tmp_path / "chrome-profile" / "Default").mkdir(parents=True)
    (tmp_path / "chrome-profile" / "Default" / "Cookies").write_bytes(b"jar")

    mock_client = MagicMock()
    mock_client.upload_state_backup = AsyncMock(return_value=True)
    mock_mgr = MagicMock()
    mock_mgr.stop = MagicMock(return_value=("completed", None))

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_cmd")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="stop",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    assert err is None
    mock_mgr.stop.assert_called_once()
    assert mock_client.upload_state_backup.await_count == 1
    archive = mock_client.upload_state_backup.await_args.args[0]
    assert isinstance(archive, bytes)
    assert archive[:2] == b"\x1f\x8b"


async def test_disconnect_also_uploads_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    (tmp_path / "chrome-profile" / "Default").mkdir(parents=True)
    (tmp_path / "chrome-profile" / "Default" / "Cookies").write_bytes(b"jar")

    mock_client = MagicMock()
    mock_client.upload_state_backup = AsyncMock(return_value=True)
    mock_mgr = MagicMock()
    mock_mgr.stop = MagicMock(return_value=("completed", None))

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_cmd2")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="disconnect",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    assert mock_client.upload_state_backup.await_count == 1


async def test_stop_skips_upload_when_nothing_to_back_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty archive must not travel: it would overwrite the cloud's only backup."""
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))

    mock_client = MagicMock()
    mock_client.upload_state_backup = AsyncMock(return_value=True)
    mock_mgr = MagicMock()
    mock_mgr.stop = MagicMock(return_value=("completed", None))

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_cmd_empty")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="stop",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    assert err is None
    mock_client.upload_state_backup.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_browser_delegates_to_container_manager(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_mgr = MagicMock()
    mock_mgr.open_browser = MagicMock(return_value=("completed", None))

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_open_browser")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="open_browser",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    assert err is None
    mock_mgr.open_browser.assert_called_once()


@pytest.mark.asyncio
async def test_open_browser_normalizes_command_type_string(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_mgr = MagicMock()
    mock_mgr.open_browser = MagicMock(return_value=("completed", None))

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_open_browser_norm")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="  OPEN_BROWSER  ",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    assert err is None
    mock_mgr.open_browser.assert_called_once()


@pytest.mark.asyncio
async def test_close_browser_delegates_to_container_manager(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_mgr = MagicMock()
    mock_mgr.close_browser = MagicMock(return_value=("completed", None))

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(1, thread_name_prefix="edge_close_browser")
    try:
        outcome, err = await _execute_remote_command(
            loop=loop,
            executor=executor,
            cmd_type="close_browser",
            client=mock_client,
            data_dir=tmp_path,
            container_mgr=mock_mgr,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcome == "completed"
    assert err is None
    mock_mgr.close_browser.assert_called_once()
