from __future__ import annotations

import os
import sys
import tarfile
from pathlib import Path

import httpx

from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token_from_data_dir
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url
from sellerclaw_agent.cloud.state_backup import (
    default_openclaw_state_dir,
    restore_state_backup,
    state_dir_has_restoreable_data,
)
from sellerclaw_agent.cloud.whatsapp_session_backup import (
    has_local_whatsapp_session,
    restore_whatsapp_session,
)
from sellerclaw_agent.http_clients import sync_client


def _resolve_restore_bearer() -> str | None:
    data_dir = Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    return resolve_agent_bearer_token_from_data_dir(data_dir)


def _download(token: str, path: str) -> bytes | None:
    base = get_sellerclaw_api_url().rstrip("/")
    timeout = httpx.Timeout(120.0, connect=10.0)
    with sync_client(timeout=timeout) as client:
        response = client.get(f"{base}{path}", headers={"Authorization": f"Bearer {token}"})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def restore_browser_sign_ins(token: str, state_dir: Path) -> None:
    """Download the browser sign-in backup when this machine has none (managed cold start)."""
    # start_agent(restart_agent(..., clean=True)) sets RESET_STATE=1; the entrypoint clears
    # sessions and memory before this runs. Skipping the restore keeps a clean start clean.
    if (os.environ.get("RESET_STATE") or "").strip() == "1":
        return
    if state_dir_has_restoreable_data(state_dir):
        return
    archive = _download(token, "/agent/connection/state-backup")
    if archive is None:
        return
    outcome = restore_state_backup(state_dir, archive)
    print(
        f"[restore_state] restored {outcome.restored} browser sign-in file(s), "
        f"dropped {outcome.skipped} file(s) outside the allowlist"
    )


def restore_whatsapp_session_if_missing(token: str, state_dir: Path) -> None:
    """Bring back the linked WhatsApp session when this machine has none.

    Independent of the browser restore, and done on a clean start too: the session is the
    assistant's identity on WhatsApp, not memory — without it the owner would scan a new QR.
    A session already on disk is newer than any copy and wins.
    """
    if has_local_whatsapp_session(state_dir):
        return
    archive = _download(token, "/agent/connection/whatsapp/session")
    if archive is None:
        return
    restored = restore_whatsapp_session(state_dir, archive)
    print(f"[restore_state] restored {restored} WhatsApp session file(s)")


def run_restore_if_needed() -> None:
    token = _resolve_restore_bearer()
    if not token:
        return
    state_dir = default_openclaw_state_dir()
    try:
        restore_whatsapp_session_if_missing(token, state_dir)
    except (httpx.HTTPError, OSError, ValueError, tarfile.TarError) as exc:
        # One category failing must not cost the other.
        print(f"[restore_state] WhatsApp session restore failed: {exc}", file=sys.stderr)
    restore_browser_sign_ins(token, state_dir)


def main() -> None:
    try:
        run_restore_if_needed()
    except httpx.HTTPError as exc:
        print(f"[restore_state] HTTP error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"[restore_state] I/O error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
