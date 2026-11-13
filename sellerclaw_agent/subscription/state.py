"""What this machine runs on: the owner's choice and its last observed status.

Two small JSON files under ``<data dir>/ai-subscription/``, neither holding a secret:

- ``state.json`` — the choice (``{"provider": "claude", "connected_at": …}``), written by
  ``connect`` / ``disconnect``. No file = credits.
- ``status.json`` — what the monitor last saw (connected / limit reached / signed out, reset
  time). The OpenClaw plugin reads it to hold turns while the agent waits.

The sign-in itself belongs to the provider's own tooling (Claude Code keeps it in its config dir
next to these files); nothing here reads it.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Subscriptions an owner can sign in to on this machine. A manifest lineup for any other key is
#: ignored, so the cloud can add a provider before every agent knows it.
SUBSCRIPTION_PROVIDERS: tuple[str, ...] = ("chatgpt", "claude")

SUBSCRIPTION_DIR_NAME = "ai-subscription"
STATE_FILE_NAME = "state.json"
STATUS_FILE_NAME = "status.json"


def subscription_dir(data_dir: Path) -> Path:
    return data_dir / SUBSCRIPTION_DIR_NAME


def status_path(data_dir: Path) -> Path:
    return subscription_dir(data_dir) / STATUS_FILE_NAME


@dataclass(frozen=True)
class SubscriptionChoice:
    """The subscription the owner connected on this machine."""

    provider: str
    connected_at: datetime


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` so a reader never sees half a file (owner-only permissions)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Per thread too: the agent server writes the same file from its request and monitor threads.
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
    os.replace(tmp_path, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_choice(data_dir: Path) -> SubscriptionChoice | None:
    """The connected subscription, or ``None`` (credits, or an unreadable / unknown choice)."""
    data = read_json(subscription_dir(data_dir) / STATE_FILE_NAME)
    if data is None:
        return None
    provider = data.get("provider")
    if provider not in SUBSCRIPTION_PROVIDERS:
        return None
    try:
        connected_at = datetime.fromisoformat(str(data.get("connected_at")))
    except ValueError:
        connected_at = datetime.fromtimestamp(0, tz=UTC)
    return SubscriptionChoice(provider=str(provider), connected_at=connected_at)


def save_choice(data_dir: Path, provider: str, *, now: datetime | None = None) -> SubscriptionChoice:
    if provider not in SUBSCRIPTION_PROVIDERS:
        raise ValueError(f"Unknown subscription {provider!r}; expected one of {SUBSCRIPTION_PROVIDERS}")
    choice = SubscriptionChoice(provider=provider, connected_at=now or datetime.now(tz=UTC))
    atomic_write_json(
        subscription_dir(data_dir) / STATE_FILE_NAME,
        {"provider": choice.provider, "connected_at": choice.connected_at.isoformat()},
    )
    return choice


def clear_choice(data_dir: Path) -> None:
    """Back to credits: forget the choice and the status the plugin would hold turns on."""
    for name in (STATE_FILE_NAME, STATUS_FILE_NAME):
        with contextlib.suppress(FileNotFoundError):
            (subscription_dir(data_dir) / name).unlink()
