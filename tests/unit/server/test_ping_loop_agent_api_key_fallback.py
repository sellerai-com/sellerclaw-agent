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
    ("file_creds", "env_token", "expect_connect"),
    [
        pytest.param(None, "sca_env_token", True, id="env-only-fallback"),
        pytest.param(MagicMock(agent_token="sca_file_token"), None, True, id="file-creds-present"),
        pytest.param(None, None, False, id="no-credentials-no-connect"),
    ],
)
async def test_ping_loop_uses_agent_api_key_when_file_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    file_creds: object | None,
    env_token: str | None,
    expect_connect: bool,
) -> None:
    """Edge ping loop must reach client.connect() when AGENT_API_KEY is set even without agent_token.json."""
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    if env_token is None:
        monkeypatch.delenv("AGENT_API_KEY", raising=False)
    else:
        monkeypatch.setenv("AGENT_API_KEY", env_token)

    stop = asyncio.Event()
    registry = EdgeRuntimeRegistry()
    command_queue: asyncio.Queue[RemoteCommandWork] = asyncio.Queue(maxsize=8)
    result_store = CommandResultStore()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test_supervisor")

    inst_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    creds_storage = MagicMock()
    creds_storage.load = MagicMock(return_value=file_creds)

    session_storage = MagicMock()
    session_storage.load = MagicMock(return_value=None)
    session_storage.save = MagicMock()
    session_storage.clear = MagicMock()

    mock_client = MagicMock()
    mock_client.connect = AsyncMock(return_value=MagicMock(agent_instance_id=inst_id))
    mock_client.ping = AsyncMock(return_value=MagicMock(pending_command=None))

    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.CredentialsStorage",
        MagicMock(return_value=creds_storage),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.EdgeSessionStorage",
        MagicMock(return_value=session_storage),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.SellerClawConnectionClient",
        MagicMock(return_value=mock_client),
    )
    fake_mgr = MagicMock()
    fake_mgr.probe_openclaw_status = MagicMock(return_value=("stopped", None))
    fake_mgr.probe_browser_status = MagicMock(
        return_value=MagicMock(status="idle", kasmvnc_running=False, chrome_running=False, error=None, pages=()),
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.create_supervisor_manager",
        MagicMock(return_value=fake_mgr),
    )

    task = asyncio.create_task(
        run_edge_ping_loop(
            stop,
            command_queue=command_queue,
            result_store=result_store,
            supervisor_executor=executor,
            registry=registry,
        ),
    )
    try:
        for _ in range(50):
            await asyncio.sleep(0.02)
            if mock_client.connect.await_count >= 1:
                break
        assert mock_client.connect.await_count == (1 if expect_connect else 0)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
        executor.shutdown(wait=False, cancel_futures=True)
