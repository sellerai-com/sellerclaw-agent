from __future__ import annotations

import os


def get_sellerclaw_api_url() -> str:
    """Base URL of the SellerClaw server (no trailing slash).

    Set via ``SELLERCLAW_API_URL``. Default targets a host API from inside Docker.
    """
    raw = os.environ.get("SELLERCLAW_API_URL", "http://host.docker.internal:8000")
    return raw.strip().rstrip("/")


def get_sellerclaw_web_url() -> str:
    """Base URL of the SellerClaw website (hosts ``/auth/device`` page).

    Set via ``SELLERCLAW_WEB_URL``. Defaults to ``http://localhost:5173``.
    """
    raw = os.environ.get("SELLERCLAW_WEB_URL", "http://localhost:5173")
    return raw.strip().rstrip("/")


def get_admin_url() -> str:
    """Base URL of the admin UI (used as CORS origin for the agent HTTP API).

    Set via ``ADMIN_URL``. Defaults to ``http://localhost:5174``.
    """
    raw = os.environ.get("ADMIN_URL", "http://localhost:5174")
    return raw.strip().rstrip("/")
