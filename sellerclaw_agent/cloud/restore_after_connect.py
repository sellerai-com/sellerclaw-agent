"""Bring browser sign-ins and the WhatsApp session back once the agent first connects.

The entrypoint restores both at container start (``restore_state``), but only when a token is
already on disk. A fresh install — the owner moving the agent from SellerClaw's cloud to this
computer — signs in later, so its first accepted ``connect`` is the earliest moment the cloud copies
can be read. Without this they would arrive only after the next container restart, and the agent
would start on this computer signed out of every site and of WhatsApp.

Once per sign-in: a marker in the data dir names the token (by a hash) the restore ran for, so a
reconnect after a network blip does not download again, while a new sign-in (another account, a
token revoked and issued again) does. Only while OpenClaw is stopped: files must not change under
a running gateway and browser. Each category still checks itself (a profile or a session already
on disk is newer than any copy and wins).

START waits for it, but not for long: the cloud expects a command answer within 60 s, so after
:data:`START_WAIT_S` the agent starts without it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import structlog

from sellerclaw_agent.cloud.state_backup import default_openclaw_state_dir, state_dir_has_restoreable_data
from sellerclaw_agent.cloud.whatsapp_session_backup import has_local_whatsapp_session

_log = structlog.get_logger(__name__)

MARKER_FILE_NAME: Final[str] = "state_restored_after_connect.json"
#: How long a START command waits for a restore in progress before starting without it.
START_WAIT_S: Final[float] = 40.0


def _default_restore() -> None:
    from sellerclaw_agent.cloud.restore_state import run_restore_if_needed

    run_restore_if_needed()


def token_fingerprint(token: str) -> str:
    """Short hash naming a sign-in in the marker; the token itself is never written there."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


class PostConnectRestore:
    """At most one restore per agent process, started by the ping loop on its first connect."""

    def __init__(
        self,
        *,
        data_dir: Path,
        state_dir: Path | None = None,
        restore: Callable[[], None] = _default_restore,
    ) -> None:
        self._marker = data_dir / MARKER_FILE_NAME
        self._state_dir = state_dir
        self._restore = restore
        self._task: asyncio.Task[None] | None = None

    def _state(self) -> Path:
        return self._state_dir or default_openclaw_state_dir()

    def _done_for(self, fingerprint: str) -> bool:
        try:
            raw = json.loads(self._marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(raw, dict) and raw.get("token_fingerprint") == fingerprint

    def _mark_done(self, fingerprint: str) -> None:
        payload = {"token_fingerprint": fingerprint, "restored_at": datetime.now(UTC).isoformat()}
        try:
            self._marker.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            _log.warning("post_connect_restore_marker_write_failed", error=str(exc)[:300])

    def _nothing_missing(self) -> bool:
        state_dir = self._state()
        return state_dir_has_restoreable_data(state_dir) and has_local_whatsapp_session(state_dir)

    def maybe_start(self, *, token: str | None, openclaw_status: str) -> bool:
        """Start the restore in the background when this sign-in has not had one; True when started.

        Started means checked: what is already on disk is looked at in the background too (it walks
        OpenClaw's state directory), and nothing is downloaded when nothing is missing.
        """
        if self._task is not None or not token:
            return False
        if openclaw_status in ("running", "starting"):
            # A reconnect with the agent already up: nothing may be written under it.
            return False
        fingerprint = token_fingerprint(token)
        if self._done_for(fingerprint):
            return False
        self._task = asyncio.create_task(self._run(fingerprint), name="post_connect_restore")
        return True

    def _restore_if_missing(self) -> bool:
        if self._nothing_missing():
            return False
        self._restore()
        return True

    async def _run(self, fingerprint: str) -> None:
        try:
            restored = await asyncio.to_thread(self._restore_if_missing)
        except Exception as exc:  # noqa: BLE001 - a failed restore leaves the agent signed out, never down
            _log.warning("post_connect_restore_failed", error=str(exc)[:500])
            return
        self._mark_done(fingerprint)
        if restored:
            _log.info("post_connect_restore_finished")

    async def wait(self, timeout_s: float = START_WAIT_S) -> bool:
        """Wait for a restore in progress; False when it is still running after ``timeout_s``."""
        task = self._task
        if task is None or task.done():
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        except TimeoutError:
            _log.warning("post_connect_restore_still_running_starting_anyway", waited_s=timeout_s)
            return False
        return True


_restore: PostConnectRestore | None = None


def install_post_connect_restore(restore: PostConnectRestore | None) -> None:
    global _restore  # noqa: PLW0603
    _restore = restore


def get_post_connect_restore() -> PostConnectRestore | None:
    return _restore
