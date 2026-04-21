"""Incoming control-plane API key for port 8001 (separate from ``AGENT_API_KEY`` / cloud token)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

_LOCAL_API_KEY_CACHE: str | None = None


def reset_local_api_key_cache() -> None:
    """Clear cached key (for tests)."""
    global _LOCAL_API_KEY_CACHE
    _LOCAL_API_KEY_CACHE = None


def load_or_create_local_api_key(data_dir: Path) -> str:
    """Return key from ``SELLERCLAW_LOCAL_API_KEY``, else ``data_dir/local_api_key``, else generate."""
    env = (os.environ.get("SELLERCLAW_LOCAL_API_KEY") or "").strip()
    if env:
        return env

    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "local_api_key"
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return raw

    token = secrets.token_urlsafe(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def get_local_api_key(data_dir: Path) -> str:
    """Return cached or load/create local API key for this data directory."""
    global _LOCAL_API_KEY_CACHE
    if _LOCAL_API_KEY_CACHE is not None:
        return _LOCAL_API_KEY_CACHE
    _LOCAL_API_KEY_CACHE = load_or_create_local_api_key(data_dir)
    return _LOCAL_API_KEY_CACHE
