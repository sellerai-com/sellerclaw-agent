from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import structlog

from sellerclaw_agent import __version__
from sellerclaw_agent.async_backoff import (
    ping_interval_after_error,
    ping_interval_success,
    ping_interval_when_suspended,
    sleep_until,
)
from sellerclaw_agent.cloud.connection_client import SellerClawConnectionClient
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import CloudAgentSuspendedError, CloudAuthError, CloudConnectionError
from sellerclaw_agent.cloud.supervisor_manager import (
    BrowserStatusProbe,
    SupervisorContainerManager,
    create_supervisor_manager,
)
from sellerclaw_agent.server.edge_commands import (
    CommandResultStore,
    CompletedRemoteCommand,
    RemoteCommandWork,
)
from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry

_log = structlog.get_logger(__name__)

AGENT_PROTOCOL_VERSION = 2


def _browser_ping_payload(probe: BrowserStatusProbe) -> dict[str, object]:
    pages = [{"url": p.url, "title": p.title, "type": p.page_type} for p in (probe.pages or ())]
    return {
        "status": probe.status,
        "kasmvnc_running": probe.kasmvnc_running,
        "chrome_running": probe.chrome_running,
        "error": probe.error,
        "pages": pages,
    }


_PERIODIC_STATE_BACKUP_SECONDS = 4 * 3600


async def run_edge_ping_loop(
    stop: asyncio.Event,
    *,
    command_queue: asyncio.Queue[RemoteCommandWork],
    result_store: CommandResultStore,
    supervisor_executor: ThreadPoolExecutor,
    registry: EdgeRuntimeRegistry,
) -> None:
    """Background task: maintain SellerClaw edge session; commands run in executor task."""
    data_dir = Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    creds_storage = CredentialsStorage(data_dir)
    session_storage = EdgeSessionStorage(data_dir)
    client = SellerClawConnectionClient(credentials_storage=creds_storage)
    container_mgr = create_supervisor_manager()
    loop = asyncio.get_running_loop()
    openclaw_status = os.environ.get("SELLERCLAW_REPORTED_OPENCLAW_STATUS", "stopped")
    openclaw_error = os.environ.get("SELLERCLAW_REPORTED_OPENCLAW_ERROR") or None

    consecutive_errors = 0
    dispatched_command_id: UUID | None = registry.get_last_dispatched_command_id()
    last_backup_at = time.monotonic()

    while not stop.is_set():
        sleep_seconds = ping_interval_success()
        creds = creds_storage.load()
        if creds is None:
            await sleep_until(stop, sleep_seconds)
            continue

        pending_ack = await result_store.get_pending_ack()
        if pending_ack is not None:
            ok = await _flush_command_ack(
                pending_ack=pending_ack,
                client=client,
                credentials_storage=creds_storage,
                session_storage=session_storage,
                container_mgr=container_mgr,
                loop=loop,
                supervisor_executor=supervisor_executor,
                registry=registry,
            )
            if ok:
                consecutive_errors = 0
                registry.mark_ping_success()
                dispatched_command_id = None
                registry.set_last_dispatched_command_id(None)
                await result_store.clear_pending_ack()
                cmd_type = pending_ack.work.command_type
                instance_id = pending_ack.work.instance_id
                if cmd_type == "disconnect":
                    try:
                        await client.disconnect(agent_instance_id=instance_id)
                    except (CloudAuthError, CloudConnectionError) as exc:
                        _log.warning("edge_disconnect_notify_failed", error=str(exc))
                    session_storage.clear()
                    break
            else:
                consecutive_errors += 1
                sleep_seconds = ping_interval_after_error(consecutive_errors)
                await sleep_until(stop, sleep_seconds)
            continue

        openclaw_status, openclaw_error = await loop.run_in_executor(
            supervisor_executor,
            container_mgr.probe_openclaw_status,
        )
        browser_probe = await loop.run_in_executor(
            supervisor_executor,
            container_mgr.probe_browser_status,
        )
        browser_payload = _browser_ping_payload(browser_probe)

        sess = session_storage.load()
        instance_id: UUID
        try:
            if sess is None:
                conn = await client.connect(
                    agent_version=__version__,
                    protocol_version=AGENT_PROTOCOL_VERSION,
                )
                session_storage.save(
                    agent_instance_id=conn.agent_instance_id,
                    protocol_version=AGENT_PROTOCOL_VERSION,
                )
                sess = session_storage.load()
            if sess is None:
                consecutive_errors += 1
                sleep_seconds = ping_interval_after_error(consecutive_errors)
                await sleep_until(stop, sleep_seconds)
                continue

            instance_id = sess.agent_instance_id
            ping = await client.ping(
                agent_instance_id=instance_id,
                agent_version=__version__,
                protocol_version=sess.protocol_version,
                openclaw_status=openclaw_status,
                openclaw_error=openclaw_error,
                command_result=None,
                browser=browser_payload,
            )
        except CloudAgentSuspendedError:
            _log.warning("edge_agent_suspended_waiting_resume")
            await sleep_until(stop, ping_interval_when_suspended())
            continue
        except CloudAuthError as exc:
            if getattr(exc, "status_code", None) == 401:
                _log.warning("edge_session_unauthorized_clearing_local_session")
                creds_storage.clear()
                session_storage.clear()
            consecutive_errors += 1
            registry.mark_ping_error(str(exc))
            sleep_seconds = ping_interval_after_error(consecutive_errors)
            await sleep_until(stop, sleep_seconds)
            continue
        except CloudConnectionError as exc:
            _log.warning("edge_ping_connection_error", error=str(exc))
            consecutive_errors += 1
            registry.mark_ping_error(str(exc))
            sleep_seconds = ping_interval_after_error(consecutive_errors)
            await sleep_until(stop, sleep_seconds)
            continue

        consecutive_errors = 0
        registry.mark_ping_success()

        if time.monotonic() - last_backup_at >= _PERIODIC_STATE_BACKUP_SECONDS:
            state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", "/home/node/.openclaw"))

            def _light_backup(sd: Path = state_dir) -> bytes:
                from sellerclaw_agent.cloud.state_backup import build_state_backup_archive

                return build_state_backup_archive(sd, include_chrome=False)

            try:
                archive = await loop.run_in_executor(supervisor_executor, _light_backup)
                ok = await client.upload_state_backup(archive)
                if ok:
                    last_backup_at = time.monotonic()
            except Exception as exc:  # noqa: BLE001
                _log.warning("edge_state_backup_periodic_failed", error=str(exc)[:500])

        pending = ping.pending_command
        if pending is not None and dispatched_command_id != pending.command_id:
            received_at = datetime.now(UTC).isoformat()
            work = RemoteCommandWork(
                command_id=pending.command_id,
                command_type=pending.command_type,
                issued_at=pending.issued_at,
                received_at_iso=received_at,
                instance_id=instance_id,
                protocol_version=sess.protocol_version,
            )
            registry.set_last_dispatched_command_id(pending.command_id)
            dispatched_command_id = pending.command_id
            await command_queue.put(work)
            _log.info(
                "edge_command_enqueued",
                command_id=str(pending.command_id),
                command_type=pending.command_type,
            )

        sleep_seconds = ping_interval_success()
        await sleep_until(stop, sleep_seconds)


async def _flush_command_ack(
    *,
    pending_ack: CompletedRemoteCommand,
    client: SellerClawConnectionClient,
    credentials_storage: CredentialsStorage,
    session_storage: EdgeSessionStorage,
    container_mgr: SupervisorContainerManager,
    loop: asyncio.AbstractEventLoop,
    supervisor_executor: ThreadPoolExecutor,
    registry: EdgeRuntimeRegistry,
) -> bool:
    sess = session_storage.load()
    if sess is None:
        _log.warning("edge_command_ack_skipped_no_session")
        return False

    instance_id = pending_ack.work.instance_id
    if sess.agent_instance_id != instance_id:
        _log.warning("edge_command_ack_skipped_session_mismatch")
        return False

    openclaw_status, openclaw_error = await loop.run_in_executor(
        supervisor_executor,
        container_mgr.probe_openclaw_status,
    )
    browser_probe = await loop.run_in_executor(
        supervisor_executor,
        container_mgr.probe_browser_status,
    )
    browser_payload = _browser_ping_payload(browser_probe)
    result_payload = {
        "command_id": str(pending_ack.work.command_id),
        "outcome": pending_ack.outcome,
        "error": pending_ack.error,
    }
    try:
        await client.ping(
            agent_instance_id=instance_id,
            agent_version=__version__,
            protocol_version=pending_ack.work.protocol_version,
            openclaw_status=openclaw_status,
            openclaw_error=openclaw_error,
            command_result=result_payload,
            browser=browser_payload,
        )
    except CloudAgentSuspendedError:
        _log.warning("edge_ping_ack_suspended")
        registry.mark_ping_error("agent_suspended")
        return False
    except CloudAuthError as exc:
        if getattr(exc, "status_code", None) == 401:
            credentials_storage.clear()
            session_storage.clear()
        _log.warning("edge_ping_ack_auth_error", error=str(exc))
        registry.mark_ping_error(str(exc))
        return False
    except CloudConnectionError as exc:
        _log.warning("edge_ping_ack_failed", error=str(exc))
        registry.mark_ping_error(str(exc))
        return False

    return True
