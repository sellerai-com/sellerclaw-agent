from __future__ import annotations

import os


def get_sellerclaw_api_url() -> str:
    """Base URL of the SellerClaw server (no trailing slash).

    Set via ``SELLERCLAW_API_URL``. Default targets a host API from inside Docker.
    """
    raw = os.environ.get("SELLERCLAW_API_URL", "http://host.docker.internal:8000")
    return raw.strip().rstrip("/")


def get_openclaw_version() -> str | None:
    """Version of the OpenClaw runtime shipped in this image, or ``None`` if unknown.

    Baked in at build time (``runtime/Dockerfile`` -> ``ENV OPENCLAW_VERSION``). Reported to
    the cloud on every ping and stamped into the generated config as ``meta.lastTouchedVersion``.
    """
    raw = (os.environ.get("OPENCLAW_VERSION") or "").strip()
    return raw or None


def get_sellerclaw_web_url() -> str:
    """Base URL of the SellerClaw website (hosts ``/auth/device`` page).

    Set via ``SELLERCLAW_WEB_URL``. Defaults to ``http://localhost:5173``.
    """
    raw = os.environ.get("SELLERCLAW_WEB_URL", "http://localhost:5173")
    return raw.strip().rstrip("/")
