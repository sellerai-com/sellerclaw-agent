"""Local media upload proxy: OpenClaw plugin → agent → cloud File Storage.

Accepts a local file path (produced by the ``browser`` tool or any other in-container
artifact), streams it to ``{cloud_api}/files/upload`` using the stored agent bearer,
and returns the public ``download_url``. This lets the ``sellerclaw-ui`` plugin attach
screenshots and other artifacts in outbound messages without the agent having to run a
separate ``curl`` step.

Auth: ``Bearer {hooks_token}`` — the same token the plugin already uses for its
internal webhook calls. The token is read from ``SELLERCLAW_DATA_DIR/secrets.json`` (or
``SELLERCLAW_HOOKS_TOKEN``).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token_from_data_dir
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url
from sellerclaw_agent.server.secrets_store import get_secrets
from sellerclaw_agent.server.storage import ManifestStorage


ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "/home/node/.openclaw/media/",
    "/tmp/",
)

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".csv", ".md", ".json"}
)

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

_UPLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)


def _data_dir() -> Path:
    return Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))


def _extract_bearer(authorization: str | None) -> str:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = authorization[len(prefix) :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    return token


def require_hooks_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Match ``Bearer …`` against the locally stored hooks token (fail-closed)."""
    token = _extract_bearer(authorization)
    storage = ManifestStorage(_data_dir())
    if storage.load() is None:
        raise HTTPException(status_code=503, detail="manifest_not_saved")
    expected = get_secrets(_data_dir()).hooks_token
    if not expected:
        raise HTTPException(status_code=503, detail="hooks_token_missing")
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _validate_local_path(raw: str) -> Path:
    """Resolve ``raw`` inside a safe prefix; reject traversal and symlink escapes."""
    if not raw or not isinstance(raw, str):
        raise HTTPException(status_code=400, detail="local_path_required")
    try:
        resolved = Path(raw).resolve(strict=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="file_not_found") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"invalid_path: {exc}") from exc
    resolved_str = str(resolved)
    if not any(resolved_str.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
        raise HTTPException(status_code=403, detail="path_not_allowed")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="not_a_file")
    return resolved


def _validate_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"extension_not_allowed: {ext}")
    return ext


def _read_bounded(path: Path) -> bytes:
    size = path.stat().st_size
    if size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="file_too_large")
    return path.read_bytes()


def _content_type_for(ext: str) -> str:
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".txt": "text/plain; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".json": "application/json",
    }
    return mapping.get(ext, "application/octet-stream")


class UploadLocalRequest(BaseModel):
    local_path: str = Field(..., min_length=1)
    filename: str | None = None


class UploadLocalResponse(BaseModel):
    file_id: str
    filename: str
    content_type: str
    size_bytes: int
    download_url: str
    expires_at: str


router = APIRouter(
    prefix="/internal/openclaw/media",
    dependencies=[Depends(require_hooks_token)],
)


async def _proxy_to_cloud(
    *,
    content: bytes,
    filename: str,
    content_type: str,
    bearer: str,
) -> dict[str, Any]:
    url = f"{get_sellerclaw_api_url().rstrip('/')}/files/upload"
    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {bearer}"},
            files={"file": (filename, content, content_type)},
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "cloud_upload_failed",
                "status": response.status_code,
                "body": response.text[:500],
            },
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="cloud_response_not_json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="cloud_response_malformed")
    return payload


@router.post("/upload-local", response_model=UploadLocalResponse)
async def upload_local_file(payload: UploadLocalRequest) -> UploadLocalResponse:
    """Read a local file inside the container and upload it to cloud File Storage."""
    resolved = _validate_local_path(payload.local_path)
    filename = (payload.filename or resolved.name).strip() or resolved.name
    ext = _validate_extension(filename)
    content = _read_bounded(resolved)
    bearer = resolve_agent_bearer_token_from_data_dir(_data_dir())
    if bearer is None:
        raise HTTPException(status_code=503, detail="agent_not_authenticated")
    cloud = await _proxy_to_cloud(
        content=content,
        filename=filename,
        content_type=_content_type_for(ext),
        bearer=bearer,
    )
    try:
        return UploadLocalResponse(
            file_id=str(cloud["file_id"]),
            filename=str(cloud["filename"]),
            content_type=str(cloud["content_type"]),
            size_bytes=int(cloud["size_bytes"]),
            download_url=str(cloud["download_url"]),
            expires_at=str(cloud["expires_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="cloud_response_missing_fields") from exc
