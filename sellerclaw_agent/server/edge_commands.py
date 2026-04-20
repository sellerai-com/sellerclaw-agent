from __future__ import annotations

import asyncio
import functools
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import structlog

from sellerclaw_agent import __version__
from sellerclaw_agent.bundle.manifest import bundle_manifest_from_mapping
from sellerclaw_agent.cloud.auth_client import SellerClawAuthClient
from sellerclaw_agent.cloud.connection_client import SellerClawConnectionClient
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.supervisor_manager import SupervisorContainerManager, create_supervisor_manager
from sellerclaw_agent.server.command_history import CommandHistoryStorage
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry
from sellerclaw_agent.server.storage import ManifestStorage

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RemoteCommandWork:
    command_id: UUID
    command_type: str
    issued_at: datetime
    received_at_iso: str
    instance_id: UUID
    protocol_version: int


@dataclass
class CompletedRemoteCommand:
    work: RemoteCommandWork
    outcome: str
    error: str | None
    executed_at_iso: str


class CommandResultStore:
    """Holds at most one completed command waiting for ping-loop ack.

    ``set_pending_ack`` blocks until the previous pending result is cleared by the
    ping loop so a fast second command cannot overwrite an unacknowledged result.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: CompletedRemoteCommand | None = None
        self._can_accept_next = asyncio.Event()
        self._can_accept_next.set()

    async def set_pending_ack(self, completed: CompletedRemoteCommand) -> None:
        await self._can_accept_next.wait()
        async with self._lock:
            self._pending = completed
            self._can_accept_next.clear()

    async def get_pending_ack(self) -> CompletedRemoteCommand | None:
        async with self._lock:
            return self._pending

    async def clear_pending_ack(self) -> None:
        async with self._lock:
            self._pending = None
        self._can_accept_next.set()


async def _execute_remote_command(
    *,
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
    cmd_type: str,
    client: SellerClawConnectionClient,
    data_dir: Path,
    container_mgr: SupervisorContainerManager,
) -> tuple[str, str | None]:
    if cmd_type in {"disconnect", "stop"}:
        outcome, err = await loop.run_in_executor(executor, container_mgr.stop)
        if outcome == "completed":
            state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", "/home/node/.openclaw"))

            def _full_backup(sd: Path = state_dir) -> bytes:
                from sellerclaw_agent.cloud.state_backup import build_state_backup_archive

                return build_state_backup_archive(sd, include_chrome=True)

            try:
                archive = await loop.run_in_executor(executor, _full_backup)
                await client.upload_state_backup(archive)
            except Exception as exc:  # noqa: BLE001
                _log.warning("edge_state_backup_after_stop_failed", error=str(exc)[:500])
        return outcome, err
    if cmd_type in {"start", "restart"}:
        try:
            mapping = await client.fetch_edge_manifest()
        except Exception as exc:  # noqa: BLE001 - surface as command failure
            _log.warning("fetch_edge_manifest_failed", error=str(exc)[:500])
            return "failed", str(exc)[:500]
        storage = ManifestStorage(data_dir)
        try:
            storage.save(mapping)
        except OSError as exc:
            return "failed", str(exc)[:500]
        try:
            manifest = bundle_manifest_from_mapping(mapping)
        except (TypeError, ValueError) as exc:
            return "failed", str(exc)[:500]
        if cmd_type == "start":
            fn = functools.partial(container_mgr.start, manifest)
        else:
            fn = functools.partial(container_mgr.restart, manifest)
        return await loop.run_in_executor(executor, fn)
    if cmd_type == "update_manifest":
        return (
            "failed",
            "update_manifest is no longer supported; use start or restart",
        )
    return "failed", f"unknown command_type {cmd_type!r}"


async def run_edge_command_executor_loop(
    *,
    stop: asyncio.Event,
    command_queue: asyncio.Queue[RemoteCommandWork],
    result_store: CommandResultStore,
    supervisor_executor: ThreadPoolExecutor,
    registry: EdgeRuntimeRegistry,
) -> None:
    """Drain ``command_queue`` and publish results for the ping loop to ack."""
    data_dir = Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    creds_storage = CredentialsStorage(data_dir)
    history_storage = CommandHistoryStorage(data_dir)
    client = SellerClawConnectionClient(
        credentials_storage=creds_storage,
        auth_client=SellerClawAuthClient(),
    )
    container_mgr = create_supervisor_manager()
    loop = asyncio.get_running_loop()

    while not stop.is_set():
        try:
            work = await asyncio.wait_for(command_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        registry.mark_executor_command(command_id=work.command_id)
        _log.info(
            "edge_command_started",
            command_id=str(work.command_id),
            command_type=work.command_type,
        )
        try:
            outcome, err = await _execute_remote_command(
                loop=loop,
                executor=supervisor_executor,
                cmd_type=work.command_type,
                client=client,
                data_dir=data_dir,
                container_mgr=container_mgr,
            )
        finally:
            registry.mark_executor_command(command_id=None)

        executed_at = datetime.now(UTC).isoformat()
        _log.info(
            "edge_command_finished",
            command_id=str(work.command_id),
            command_type=work.command_type,
            outcome=outcome,
            error=err,
        )

        try:
            history_storage.append(
                {
                    "command_id": str(work.command_id),
                    "command_type": work.command_type,
                    "issued_at": work.issued_at.isoformat(),
                    "received_at": work.received_at_iso,
                    "executed_at": executed_at,
                    "outcome": outcome,
                    "error": err,
                }
            )
        except OSError as exc:
            _log.warning("command_history_append_failed", error=str(exc))

        await result_store.set_pending_ack(
            CompletedRemoteCommand(
                work=work,
                outcome=outcome,
                error=err,
                executed_at_iso=executed_at,
            ),
        )

    _log.info("edge_command_executor_stopped", agent_version=__version__)
