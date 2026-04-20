from __future__ import annotations

import os


def get_sellerclaw_api_url() -> str:
    """Base URL of the SellerClaw server (no trailing slash).

    Set via ``SELLERCLAW_API_URL``. Default targets a host API from inside Docker.
    """
    raw = os.environ.get("SELLERCLAW_API_URL", "http://host.docker.internal:8000")
    return raw.strip().rstrip("/")


def get_verification_base_url() -> str:
    """Base URL of the UI that hosts ``/auth/device`` verification page.

    Set via ``AGENT_VERIFICATION_BASE_URL``.
    Defaults to the dev-admin UI at ``http://localhost:5173``.
    """
    raw = os.environ.get("AGENT_VERIFICATION_BASE_URL", "http://localhost:5173")
    return raw.strip().rstrip("/")
