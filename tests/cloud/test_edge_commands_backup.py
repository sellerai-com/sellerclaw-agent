from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sellerclaw_agent.server.edge_commands import _execute_remote_command

pytestmark = pytest.mark.unit


async def test_stop_uploads_full_state_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    (tmp_path / "agents" / "x" / "sessions").mkdir(parents=True)
    (tmp_path / "agents" / "x" / "sessions" / "s.jsonl").write_text("[]\n", encoding="utf-8")
    (tmp_path / "chrome-profile" / "Default").mkdir(parents=True)
    (tmp_path / "chrome-profile" / "Default" / "prefs").write_bytes(b"{}")

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
    (tmp_path / "agents" / "x" / "sessions").mkdir(parents=True)
    (tmp_path / "agents" / "x" / "sessions" / "s.jsonl").write_text("[]\n", encoding="utf-8")

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
