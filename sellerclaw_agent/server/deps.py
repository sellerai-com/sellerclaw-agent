"""FastAPI dependencies for the edge agent HTTP API."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException

from sellerclaw_agent.server.local_api_key import get_local_api_key


def _data_dir() -> Path:
    return Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))


def require_local_api_key(authorization: str | None = Header(default=None)) -> None:
    """Validate ``Authorization: Bearer`` against local control-plane key (fail-closed)."""
    expected = get_local_api_key(_data_dir())
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = authorization[len(prefix) :].strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
