"""Ping loop, protocol 10: where the agent runs, refusals that name the owner's agent, a revoked token."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sellerclaw_agent.cloud.cron_standby import CronStandby
from sellerclaw_agent.cloud.exceptions import (
    CloudAgentMoveInProgressError,
    CloudAgentRunsInCloudError,
    CloudAgentRunsOnComputerError,
    CloudAuthError,
    CloudConnectionAlreadyActiveError,
    CloudConnectionError,
)
from sellerclaw_agent.server.edge_commands import CommandResultStore, CompletedRemoteCommand, RemoteCommandWork
from sellerclaw_agent.server.ping_loop import AGENT_PROTOCOL_VERSION, run_edge_ping_loop
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

pytestmark = pytest.mark.unit

_INSTANCE = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


class _NoPlugin:
    async def post_cron_standby_json(self, *, standby: bool) -> tuple[bool, dict[str, Any]]:
        return True, {"ok": True}


@dataclass
class _FakeRestore:
    """Records what the ping loop asked of the post-connect restore."""

    started: list[dict[str, Any]] = field(default_factory=list)

    def maybe_start(self, *, token: str | None, openclaw_status: str) -> bool:
        self.started.append({"token": token, "openclaw_status": openclaw_status})
        return True


@dataclass
class _Loop:
    registry: EdgeRuntimeRegistry
    container_mgr: MagicMock
    session_storage: MagicMock
    creds_storage: MagicMock


async def _run_loop(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: MagicMock,
    openclaw_status: str = "stopped",
    session: Any = None,
    restore: _FakeRestore | None = None,
    result_store: CommandResultStore | None = None,
) -> _Loop:
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    stop = asyncio.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test_supervisor")
    registry = EdgeRuntimeRegistry()

    session_storage = MagicMock()
    session_storage.load = MagicMock(return_value=session)
    creds_storage = MagicMock()
    creds_storage.load = MagicMock(return_value=MagicMock(agent_token="sca_test_token"))
    container_mgr = MagicMock()
    container_mgr.probe_openclaw_status = MagicMock(return_value=(openclaw_status, None))
    container_mgr.probe_browser_status = MagicMock(
        return_value=MagicMock(status="stopped", kasmvnc_running=False, chrome_running=False, error=None, pages=())
    )
    container_mgr.stop = MagicMock(return_value=("completed", None))
    activity_reader = MagicMock()
    activity_reader.probe = AsyncMock(return_value=None)

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.CredentialsStorage", MagicMock(return_value=creds_storage))
    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.EdgeSessionStorage", MagicMock(return_value=session_storage))
    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.SellerClawConnectionClient", MagicMock(return_value=client))
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.create_supervisor_manager", MagicMock(return_value=container_mgr)
    )
    monkeypatch.setattr(
        "sellerclaw_agent.server.ping_loop.create_agent_activity_reader", MagicMock(return_value=activity_reader)
    )
    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.agent_activity_ping_payload", MagicMock(return_value=None))

    async def _one_round(_stop: asyncio.Event, _seconds: float) -> None:
        stop.set()
        await asyncio.sleep(0)

    monkeypatch.setattr("sellerclaw_agent.server.ping_loop.sleep_until", _one_round)

    await asyncio.wait_for(
        run_edge_ping_loop(
            stop,
            command_queue=asyncio.Queue(maxsize=8),
            result_store=result_store or CommandResultStore(),
            supervisor_executor=executor,
            registry=registry,
            cron_standby=CronStandby(tmp_path, plugin_factory=_NoPlugin),
            post_connect_restore=restore,  # type: ignore[arg-type]
        ),
        timeout=2.0,
    )
    executor.shutdown(wait=False, cancel_futures=True)
    return _Loop(
        registry=registry,
        container_mgr=container_mgr,
        session_storage=session_storage,
        creds_storage=creds_storage,
    )


def _connecting_client() -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock(return_value=MagicMock(agent_instance_id=_INSTANCE))
    client.ping = AsyncMock(return_value=MagicMock(pending_command=None))
    return client


class _Sess:
    agent_instance_id = _INSTANCE
    protocol_version = AGENT_PROTOCOL_VERSION


@pytest.mark.parametrize(
    ("env", "probe", "expected_runs_on", "expected_status"),
    [
        pytest.param({}, "stopped", "computer", "stopped", id="computer-stopped"),
        pytest.param({}, "starting", "computer", "running", id="computer-coming-up"),
        pytest.param({"FLY_MACHINE_ID": "abc123"}, "running", "cloud", "running", id="cloud-machine-running"),
        pytest.param({"SELLERCLAW_RUNS_ON": "cloud"}, "error", "cloud", None, id="cloud-status-unknown"),
    ],
)
async def test_connect_says_where_it_runs_and_whether_openclaw_is_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env: dict[str, str],
    probe: str,
    expected_runs_on: str,
    expected_status: str | None,
) -> None:
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    monkeypatch.delenv("SELLERCLAW_RUNS_ON", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    client = _connecting_client()

    await _run_loop(monkeypatch=monkeypatch, tmp_path=tmp_path, client=client, openclaw_status=probe)

    client.connect.assert_awaited_once()
    assert client.connect.await_args.kwargs == {
        "agent_version": client.connect.await_args.kwargs["agent_version"],
        "protocol_version": 10,
        "runs_on": expected_runs_on,
        "openclaw_status": expected_status,
    }


@pytest.mark.parametrize(
    ("error", "code"),
    [
        pytest.param(CloudConnectionAlreadyActiveError("taken"), "agent_connection_already_active", id="another"),
        pytest.param(CloudAgentMoveInProgressError("handing over"), "agent_move_in_progress", id="moving"),
        pytest.param(CloudAgentRunsInCloudError("runs in cloud"), "agent_runs_in_cloud", id="runs-in-cloud"),
        pytest.param(CloudAgentRunsOnComputerError("on computer"), "agent_runs_on_computer", id="runs-on-computer"),
        pytest.param(CloudConnectionError("network down"), None, id="network-error-has-no-code"),
    ],
)
async def test_a_refused_connect_reports_its_code_in_the_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception, code: str | None
) -> None:
    client = MagicMock()
    client.connect = AsyncMock(side_effect=error)
    client.ping = AsyncMock()

    loop = await _run_loop(monkeypatch=monkeypatch, tmp_path=tmp_path, client=client)

    ping = loop.registry.snapshot_tasks()["ping_loop"]
    assert ping["last_error_code"] == code
    assert ping["last_error"] == str(error)
    assert ping["consecutive_errors"] == 1
    client.ping.assert_not_awaited()
    # Waiting, never giving up the sign-in: the token stays for the next attempt.
    loop.creds_storage.clear.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(CloudAgentRunsInCloudError("runs in cloud"), id="runs-in-cloud"),
        pytest.param(CloudAgentMoveInProgressError("handing over"), id="moving"),
    ],
)
async def test_a_refused_connect_goes_on_cron_standby(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    """A second copy of the owner's agent must not run their scheduled tasks beside the first."""
    client = MagicMock()
    client.connect = AsyncMock(side_effect=error)

    await _run_loop(monkeypatch=monkeypatch, tmp_path=tmp_path, client=client)

    assert CronStandby(tmp_path, plugin_factory=_NoPlugin).is_standby() is True


@pytest.mark.parametrize(
    ("openclaw_status", "stops"),
    [
        pytest.param("running", True, id="running-stops"),
        pytest.param("starting", True, id="coming-up-stops"),
        pytest.param("error", True, id="crash-looping-stops"),
        pytest.param("stopped", False, id="already-stopped"),
    ],
)
async def test_a_revoked_token_stops_openclaw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, openclaw_status: str, stops: bool
) -> None:
    """The agent moved elsewhere: Telegram and WhatsApp must not answer from two machines."""
    client = MagicMock()
    client.ping = AsyncMock(side_effect=CloudAuthError("Invalid agent token.", status_code=401))

    loop = await _run_loop(
        monkeypatch=monkeypatch, tmp_path=tmp_path, client=client, session=_Sess(), openclaw_status=openclaw_status
    )

    loop.creds_storage.clear.assert_called_once()
    loop.session_storage.clear.assert_called_once()
    assert loop.container_mgr.stop.call_count == (1 if stops else 0)


async def test_a_non_401_auth_error_leaves_openclaw_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = MagicMock()
    client.ping = AsyncMock(side_effect=CloudAuthError("Forbidden", status_code=403))

    loop = await _run_loop(
        monkeypatch=monkeypatch, tmp_path=tmp_path, client=client, session=_Sess(), openclaw_status="running"
    )

    loop.container_mgr.stop.assert_not_called()
    loop.creds_storage.clear.assert_not_called()


async def test_a_revoked_token_on_a_command_ack_stops_openclaw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = MagicMock()
    client.ping = AsyncMock(side_effect=CloudAuthError("Invalid agent token.", status_code=401))
    result_store = CommandResultStore()
    work = RemoteCommandWork(
        command_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        command_type="start",
        issued_at=MagicMock(),
        received_at_iso="2026-11-14T00:00:00+00:00",
        instance_id=_INSTANCE,
        protocol_version=AGENT_PROTOCOL_VERSION,
    )
    await result_store.set_pending_ack(
        CompletedRemoteCommand(work=work, outcome="completed", error=None, executed_at_iso="2026-11-14T00:00:01+00:00")
    )

    loop = await _run_loop(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        client=client,
        session=_Sess(),
        openclaw_status="running",
        result_store=result_store,
    )

    loop.creds_storage.clear.assert_called_once()
    loop.container_mgr.stop.assert_called_once()
    assert await result_store.get_pending_ack() is None


async def test_the_first_connect_starts_the_restore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fresh install signs in after the container started: sign-ins come back on connect."""
    restore = _FakeRestore()
    client = _connecting_client()

    await _run_loop(
        monkeypatch=monkeypatch, tmp_path=tmp_path, client=client, restore=restore, openclaw_status="stopped"
    )

    assert restore.started == [{"token": "sca_test_token", "openclaw_status": "stopped"}]


@pytest.mark.parametrize(
    ("session", "connect_error"),
    [
        pytest.param(_Sess(), None, id="session-already-there"),
        pytest.param(None, CloudAgentRunsInCloudError("runs in cloud"), id="connect-refused"),
    ],
)
async def test_no_restore_without_a_fresh_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, session: Any, connect_error: Exception | None
) -> None:
    restore = _FakeRestore()
    client = _connecting_client()
    if connect_error is not None:
        client.connect = AsyncMock(side_effect=connect_error)

    await _run_loop(monkeypatch=monkeypatch, tmp_path=tmp_path, client=client, session=session, restore=restore)

    assert restore.started == []
