"""Incoming control-plane API key for port 8001 (separate from ``AGENT_API_KEY`` / cloud token)."""

from __future__ import annotations

from pathlib import Path

from sellerclaw_agent.server.secrets_store import (
    get_secrets,
    load_or_create_secrets,
    reset_secrets_cache,
)


def reset_local_api_key_cache() -> None:
    """Clear cached key (for tests)."""
    reset_secrets_cache()


def load_or_create_local_api_key(data_dir: Path) -> str:
    """Return key from ``SELLERCLAW_LOCAL_API_KEY``, else ``secrets.json`` / legacy file, else generate."""
    return load_or_create_secrets(data_dir).local_api_key


def get_local_api_key(data_dir: Path) -> str:
    """Return cached or load/create local API key for this data directory."""
    return get_secrets(data_dir).local_api_key
