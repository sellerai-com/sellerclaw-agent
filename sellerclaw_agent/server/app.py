from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from sellerclaw_agent.bundle import GatewayArchivePayload, build_gateway_archive
from sellerclaw_agent.bundle.builder import BundleBuilder
from sellerclaw_agent.bundle.manifest import BundleManifest, bundle_manifest_from_mapping
from sellerclaw_agent.paths import get_agent_resources_root
from sellerclaw_agent.cloud.auth_client import SellerClawAuthClient
from sellerclaw_agent.cloud.connection_state import EdgeSessionStorage
from sellerclaw_agent.cloud.credentials import CredentialsStorage
from sellerclaw_agent.cloud.exceptions import (
    CloudAuthError,
    CloudConnectionError,
    CloudDevicePollTerminalError,
)
from sellerclaw_agent.cloud.service import AuthStatus, CloudAuthService
from sellerclaw_agent.cloud.settings import get_verification_base_url
from sellerclaw_agent.cloud.supervisor_manager import (
    REJECT_ALREADY_RUNNING,
    SupervisorContainerManager,
    create_supervisor_manager,
    write_runtime_env,
)
from sellerclaw_agent.server.command_history import CommandHistoryStorage
from sellerclaw_agent.server.deps import require_agent_api_key
from sellerclaw_agent.server.schemas import (
    AuthStatusResponse,
    CommandHistoryEntry,
    CommandHistoryResponse,
    ConnectRequest,
    DevicePollResponse,
    DeviceStartResponse,
    DisconnectResponse,
    GetManifestResponse,
    OpenClawCommandResponse,
    OpenClawStatusResponse,
    SaveManifestRequest,
    SaveManifestResponse,
)
from sellerclaw_agent.server.storage import ManifestStorage


def _edge_ping_enabled() -> bool:
    raw = (os.environ.get("SELLERCLAW_EDGE_PING", "1") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@asynccontextmanager
async def _app_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _ = _app
    from sellerclaw_agent.logging_setup import configure_agent_logging

    configure_agent_logging()

    stop = asyncio.Event()
    background_holders: list[dict[str, Any]] = []
    supervisor_executor: ThreadPoolExecutor | None = None
    if _edge_ping_enabled():
        from sellerclaw_agent.cloud.chat_listener import run_edge_chat_sse_loop
        from sellerclaw_agent.server.edge_commands import (
            CommandResultStore,
            RemoteCommandWork,
            run_edge_command_executor_loop,
        )
        from sellerclaw_agent.server.ping_loop import run_edge_ping_loop
        from sellerclaw_agent.server.runtime_registry import EdgeRuntimeRegistry, install_runtime_registry
        from sellerclaw_agent.server.task_watchdog import start_watched_background

        registry = EdgeRuntimeRegistry()
        install_runtime_registry(registry)
        command_queue: asyncio.Queue[RemoteCommandWork] = asyncio.Queue(maxsize=8)
        result_store = CommandResultStore()
        supervisor_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sellerclaw_supervisor")

        background_holders.append(
            start_watched_background(
                lambda: run_edge_ping_loop(
                    stop,
                    command_queue=command_queue,
                    result_store=result_store,
                    supervisor_executor=supervisor_executor,
                    registry=registry,
                ),
                name="ping_loop",
                stop=stop,
                registry=registry,
            ),
        )
        background_holders.append(
            start_watched_background(
                lambda: run_edge_command_executor_loop(
                    stop=stop,
                    command_queue=command_queue,
                    result_store=result_store,
                    supervisor_executor=supervisor_executor,
                    registry=registry,
                ),
                name="command_executor",
                stop=stop,
                registry=registry,
            ),
        )
        background_holders.append(
            start_watched_background(
                lambda: run_edge_chat_sse_loop(stop, registry=registry),
                name="chat_sse",
                stop=stop,
                registry=registry,
            ),
        )
    else:
        from sellerclaw_agent.server.runtime_registry import install_runtime_registry

        install_runtime_registry(None)
    try:
        yield
    finally:
        stop.set()
        from sellerclaw_agent.server.runtime_registry import install_runtime_registry

        install_runtime_registry(None)
        for holder in background_holders:
            task = holder.get("task")
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if supervisor_executor is not None:
            supervisor_executor.shutdown(wait=False, cancel_futures=True)


def _get_data_dir() -> Path:
    return Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))


def get_storage() -> ManifestStorage:
    return ManifestStorage(_get_data_dir())


def get_command_history_storage() -> CommandHistoryStorage:
    return CommandHistoryStorage(_get_data_dir())


def get_cloud_auth_service() -> CloudAuthService:
    return CloudAuthService(
        auth_client=SellerClawAuthClient(),
        credentials_storage=CredentialsStorage(_get_data_dir()),
    )


def get_openclaw_manager() -> SupervisorContainerManager:
    return create_supervisor_manager()


def _load_saved_bundle_manifest(storage: ManifestStorage) -> BundleManifest:
    data = storage.load()
    if data is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "manifest_not_found", "message": "manifest not saved yet"},
        )
    try:
        return bundle_manifest_from_mapping(data)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _openclaw_reject_message(code: str) -> str:
    if code == REJECT_ALREADY_RUNNING:
        return "OpenClaw is already running; use POST /openclaw/restart instead."
    return code


app = FastAPI(title="SellerClaw Agent", lifespan=_app_lifespan)
control_plane = APIRouter(dependencies=[Depends(require_agent_api_key)])


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness/readiness-style snapshot for the agent process and edge tasks."""
    from sellerclaw_agent.server.runtime_registry import get_runtime_registry

    registry = get_runtime_registry()
    data_dir = _get_data_dir()
    creds = CredentialsStorage(data_dir).load()
    sess = EdgeSessionStorage(data_dir).load()
    mgr = get_openclaw_manager()
    detail = await asyncio.to_thread(mgr.get_status_detail)

    session_payload = {
        "connected": creds is not None and sess is not None,
        "agent_instance_id": str(sess.agent_instance_id) if sess else None,
    }
    openclaw_payload = {
        "status": detail.get("status"),
        "container_id": detail.get("container_id"),
        "uptime_seconds": detail.get("uptime_seconds"),
        "error": detail.get("error"),
    }
    if registry is None:
        return {
            "status": "healthy",
            "edge_ping_enabled": False,
            "tasks": {},
            "openclaw": openclaw_payload,
            "session": session_payload,
        }

    tasks = registry.snapshot_tasks()
    ping_alive = bool(tasks.get("ping_loop", {}).get("alive", True))
    exec_alive = bool(tasks.get("command_executor", {}).get("alive", True))
    chat_alive = bool(tasks.get("chat_sse", {}).get("alive", True))

    if not ping_alive:
        overall = "unhealthy"
    elif not session_payload["connected"] or not exec_alive or not chat_alive:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "edge_ping_enabled": True,
        "tasks": tasks,
        "openclaw": openclaw_payload,
        "session": session_payload,
    }


_cors_origins_env = os.environ.get(
    "AGENT_CORS_ORIGINS",
    "http://localhost:5173,http://localhost:5174",
)
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _to_response(s: AuthStatus) -> AuthStatusResponse:
    return AuthStatusResponse(
        connected=s.connected,
        user_id=s.user_id,
        user_email=s.user_email,
        user_name=s.user_name,
        connected_at=s.connected_at,
    )


@control_plane.get("/manifest", response_model=GetManifestResponse)
def get_manifest(
    storage: Annotated[ManifestStorage, Depends(get_storage)],
) -> GetManifestResponse:
    loaded = storage.load_with_version()
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "manifest_not_found", "message": "manifest not saved yet"},
        )
    data, version = loaded
    return GetManifestResponse(manifest=data, version=version)


@app.post("/auth/device/start", response_model=DeviceStartResponse)
async def auth_device_start(
    service: Annotated[CloudAuthService, Depends(get_cloud_auth_service)],
) -> DeviceStartResponse:
    try:
        result = await service.start_device_flow()
    except CloudAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CloudConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    base = get_verification_base_url()
    local_uri = f"{base}/auth/device?code={result.user_code}"
    return DeviceStartResponse(
        device_code=result.device_code,
        user_code=result.user_code,
        verification_uri=local_uri,
        expires_in=result.expires_in,
        interval=result.interval,
    )


@app.get("/auth/device/poll", response_model=DevicePollResponse)
async def auth_device_poll(
    service: Annotated[CloudAuthService, Depends(get_cloud_auth_service)],
    device_code: str = Query(..., min_length=1),
) -> DevicePollResponse:
    try:
        status = await service.poll_device_flow(device_code=device_code)
    except CloudAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CloudDevicePollTerminalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CloudConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if status is None:
        return DevicePollResponse(status="pending", auth=None)
    return DevicePollResponse(status="completed", auth=_to_response(status))


@app.post("/auth/connect", response_model=AuthStatusResponse)
async def auth_connect(
    body: ConnectRequest,
    service: Annotated[CloudAuthService, Depends(get_cloud_auth_service)],
) -> AuthStatusResponse:
    try:
        status = await service.connect(email=body.email, password=body.password)
    except CloudAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CloudConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _to_response(status)


@app.get("/auth/status", response_model=AuthStatusResponse)
def auth_status(
    service: Annotated[CloudAuthService, Depends(get_cloud_auth_service)],
) -> AuthStatusResponse:
    return _to_response(service.get_status())


@app.post("/auth/disconnect", response_model=DisconnectResponse)
def auth_disconnect(
    service: Annotated[CloudAuthService, Depends(get_cloud_auth_service)],
) -> DisconnectResponse:
    service.disconnect()
    return DisconnectResponse(status="ok")


@app.get("/commands/history", response_model=CommandHistoryResponse)
def get_command_history(
    storage: Annotated[CommandHistoryStorage, Depends(get_command_history_storage)],
) -> CommandHistoryResponse:
    entries: list[CommandHistoryEntry] = []
    for raw in storage.load():
        try:
            entries.append(CommandHistoryEntry.model_validate(raw))
        except ValueError:
            continue
    return CommandHistoryResponse(entries=entries)


@control_plane.get("/openclaw/status", response_model=OpenClawStatusResponse)
def openclaw_status(
    mgr: Annotated[SupervisorContainerManager, Depends(get_openclaw_manager)],
) -> OpenClawStatusResponse:
    detail = mgr.get_status_detail()
    return OpenClawStatusResponse.model_validate(detail)


@control_plane.post("/openclaw/start", response_model=OpenClawCommandResponse)
def openclaw_start(
    storage: Annotated[ManifestStorage, Depends(get_storage)],
    mgr: Annotated[SupervisorContainerManager, Depends(get_openclaw_manager)],
) -> OpenClawCommandResponse:
    manifest = _load_saved_bundle_manifest(storage)
    outcome, err = mgr.start(manifest)
    if outcome == "rejected":
        code = err or "rejected"
        raise HTTPException(
            status_code=409,
            detail={"code": code, "message": _openclaw_reject_message(code)},
        )
    return OpenClawCommandResponse(outcome=outcome, error=err)


@control_plane.post("/openclaw/stop", response_model=OpenClawCommandResponse)
def openclaw_stop(
    mgr: Annotated[SupervisorContainerManager, Depends(get_openclaw_manager)],
) -> OpenClawCommandResponse:
    outcome, err = mgr.stop()
    return OpenClawCommandResponse(outcome=outcome, error=err)


@control_plane.post("/openclaw/restart", response_model=OpenClawCommandResponse)
def openclaw_restart(
    storage: Annotated[ManifestStorage, Depends(get_storage)],
    mgr: Annotated[SupervisorContainerManager, Depends(get_openclaw_manager)],
) -> OpenClawCommandResponse:
    manifest = _load_saved_bundle_manifest(storage)
    outcome, err = mgr.restart(manifest)
    if outcome == "rejected":
        code = err or "rejected"
        raise HTTPException(
            status_code=409,
            detail={"code": code, "message": _openclaw_reject_message(code)},
        )
    return OpenClawCommandResponse(outcome=outcome, error=err)


@control_plane.post("/manifest", response_model=SaveManifestResponse)
def save_manifest(
    body: SaveManifestRequest,
    storage: Annotated[ManifestStorage, Depends(get_storage)],
) -> SaveManifestResponse:
    mapping = body.to_mapping()
    try:
        manifest = bundle_manifest_from_mapping(mapping)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path, version = storage.save(mapping)
    bundle_volume_path = Path(
        os.environ.get("OPENCLAW_BUNDLE_VOLUME_PATH", "/opt/config-bundle")
    )
    write_runtime_env(bundle_volume_path, proxy_url=manifest.proxy_url)
    return SaveManifestResponse(
        status="ok",
        manifest_path=str(path),
        version=version,
    )


@control_plane.get("/bundle/archive")
def download_bundle_archive(
    storage: Annotated[ManifestStorage, Depends(get_storage)],
) -> Response:
    """Return gzip tar of OpenClaw config + workspaces built from the last saved manifest.

    The control plane should ``POST /manifest`` first so storage contains the latest mapping.
    """
    manifest = _load_saved_bundle_manifest(storage)
    builder = BundleBuilder(resources_root=get_agent_resources_root())
    prefix_raw = (manifest.model_name_prefix or "").strip()
    model_prefix = prefix_raw if prefix_raw else None
    result = builder.build(
        manifest,
        model_name_prefix=model_prefix,
        extra_allowed_origins=manifest.extra_allowed_origins,
    )
    archive_bytes = build_gateway_archive(
        GatewayArchivePayload(
            openclaw_config=result.openclaw_config,
            workspaces=result.workspaces,
            created_at=result.created_at,
        )
    )
    return Response(
        content=archive_bytes,
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="config-bundle.tar.gz"'},
    )


app.include_router(control_plane)

_default_admin_ui_dist = Path(__file__).resolve().parents[2] / "admin-ui" / "dist"
_admin_ui_dist = Path(os.environ.get("AGENT_ADMIN_UI_DIST", str(_default_admin_ui_dist)))
if _admin_ui_dist.is_dir():
    app.mount(
        "/admin",
        StaticFiles(directory=str(_admin_ui_dist), html=True),
        name="admin",
    )
