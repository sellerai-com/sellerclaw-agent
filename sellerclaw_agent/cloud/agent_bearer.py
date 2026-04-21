"""Resolve outbound cloud bearer (``sca_…``) consistently across modules."""

from __future__ import annotations

import os
from pathlib import Path

from sellerclaw_agent.cloud.credentials import CredentialsStorage


def resolve_agent_bearer_token(credentials_storage: CredentialsStorage) -> str | None:
    """Prefer ``agent_token.json``; fall back to ``AGENT_API_KEY``.

    File wins when present so interactive sign-in overrides a stale env value in the same process.
    """
    stored = credentials_storage.load()
    if stored is not None:
        return stored.agent_token
    env_key = (os.environ.get("AGENT_API_KEY") or "").strip()
    return env_key or None


def resolve_agent_bearer_token_from_data_dir(data_dir: Path) -> str | None:
    return resolve_agent_bearer_token(CredentialsStorage(data_dir))
