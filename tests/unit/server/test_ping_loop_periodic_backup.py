from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sellerclaw_agent.server.edge_commands import CommandResultStore, RemoteCommandWork
from sellerclaw_agent.server.ping_loop import run_edge_ping_loop
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "with_sign_ins",
    [
        pytest.param(True, id="sign-ins-present"),
        # No browser sign-ins on disk yet: uploading the resulting empty tar would
        # overwrite the cloud's only archive, so the cycle must skip the upload.
        pytest.param(False, id="nothing-to-back-up"),
    ],
)
async def test_periodic_state_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path, with_sign_ins: bool
) -> None:
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    state_root = tmp_path / "oc"
    state_root.mkdir(parents=True)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_root))
    if with_sign_ins:
        (state_root / "chrome-profile" / "Default").mkdir(parents=True)
        (state_root / "chrome-profile" / "Default" / "Cookies").write_text("jar", encoding="utf-8")

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop._PERIODIC_STATE_BACKUP_SECONDS", 0)

    stop = asyncio.Event()
    registry = EdgeRuntimeRegistry()
    command_queue: asyncio.Queue[RemoteCommandWork] = asyncio.Queue(maxsize=8)
    result_store = CommandResultStore()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test_supervisor")

    inst_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    class _Sess:
        agent_instance_id = inst_id
        protocol_version = 1

    class _Ping:
        pending_command = None

    mock_client = MagicMock()
    mock_client.connect = AsyncMock(return_value=MagicMock(agent_instance_id=inst_id))
    mock_client.ping = AsyncMock(return_value=_Ping())
    mock_client.upload_state_backup = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.CredentialsStorage",
        MagicMock(return_value=MagicMock(load=MagicMock(return_value=MagicMock(agent_token="test_agent_token")))),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.EdgeSessionStorage",
        MagicMock(return_value=MagicMock(load=MagicMock(return_value=_Sess()))),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.SellerClawConnectionClient",
        MagicMock(return_value=mock_client),
    )
    fake_mgr = MagicMock()
    fake_mgr.probe_openclaw_status = MagicMock(return_value=("stopped", None))
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.create_supervisor_manager",
        MagicMock(return_value=fake_mgr),
    )

    async def _fast_sleep(_stop: asyncio.Event, _seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.sleep_until", _fast_sleep)

    ping_task = asyncio.create_task(
        run_edge_ping_loop(
            stop,
            command_queue=command_queue,
            result_store=result_store,
            supervisor_executor=executor,
            registry=registry,
        ),
    )

    try:
        if with_sign_ins:
            for _ in range(500):
                await asyncio.sleep(0.01)
                if mock_client.upload_state_backup.await_count >= 1:
                    break
            assert mock_client.upload_state_backup.await_count >= 1
            first_archive = mock_client.upload_state_backup.await_args.args[0]
            assert isinstance(first_archive, bytes)
            assert first_archive[:2] == b"\x1f\x8b"
        else:
            # Let the loop pass the backup branch several times before concluding.
            for _ in range(500):
                await asyncio.sleep(0.01)
                if mock_client.ping.await_count >= 3:
                    break
            assert mock_client.ping.await_count >= 3
            assert mock_client.upload_state_backup.await_count == 0
    finally:
        stop.set()
        await asyncio.wait_for(ping_task, timeout=2.0)
        executor.shutdown(wait=False, cancel_futures=True)
