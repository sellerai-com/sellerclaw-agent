"""FastAPI dependencies for the edge agent HTTP API."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException


def require_agent_api_key(authorization: str | None = Header(default=None)) -> None:
    """Validate ``Authorization: Bearer`` against ``AGENT_API_KEY`` (managed machine env)."""
    expected = (os.environ.get("AGENT_API_KEY") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="agent_api_key_not_configured")
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = authorization[len(prefix) :].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")
