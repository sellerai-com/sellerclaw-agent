from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

from sellerclaw_agent.cloud.agent_bearer import resolve_agent_bearer_token_from_data_dir
from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url
from sellerclaw_agent.cloud.state_backup import (
    default_openclaw_state_dir,
    restore_state_backup,
    state_dir_has_restoreable_data,
)


def _resolve_restore_bearer() -> str | None:
    data_dir = Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    return resolve_agent_bearer_token_from_data_dir(data_dir)


def run_restore_if_needed() -> None:
    """Download latest state backup when local OpenClaw state is empty (managed cold start)."""
    # start_agent(restart_agent(..., clean=True)) sets RESET_STATE=1; entrypoint clears sessions/memory
    # before this runs. Skipping cloud restore avoids re-importing stale/corrupt data from S3.
    if (os.environ.get("RESET_STATE") or "").strip() == "1":
        return
    state_dir = default_openclaw_state_dir()
    if state_dir_has_restoreable_data(state_dir):
        return
    token = _resolve_restore_bearer()
    if not token:
        return
    base = get_sellerclaw_api_url().rstrip("/")
    url = f"{base}/agent/connection/state-backup"
    timeout = httpx.Timeout(120.0, connect=10.0)
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url, headers={"Authorization": f"Bearer {token}"})
    if response.status_code == 404:
        return
    response.raise_for_status()
    restore_state_backup(state_dir, response.content)


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
