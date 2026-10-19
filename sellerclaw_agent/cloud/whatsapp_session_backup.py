"""Cloud copy of the assistant's WhatsApp session (the linked-device credentials).

Managed machines run without a volume and one-command installs keep only ``/data``, so the
Baileys auth directory the WhatsApp plugin writes (``~/.openclaw/credentials/whatsapp/``) is
gone after every machine update. Without a copy elsewhere the owner would rescan a QR after
each release. The copy is its own cloud object, separate from the browser sign-in backup, so
neither can overwrite or delete the other, and the cloud can drop it when the owner
disconnects WhatsApp even while this machine is offline.

Baileys rewrites these files continuously (signal sessions ratchet on every message, pre-keys
get consumed), so a copy is taken only after the directory has been quiet for a while — with
an upper bound on staleness for a chatty account — and every JSON file must parse: a file
caught mid-write fails the snapshot, which is retried, instead of being uploaded torn.

Disconnect is normally carried out by the logout command (it deletes the directory and the cloud
object). That command is best-effort on the cloud's side — it is skipped while another command
holds the agent — so the loop also follows the applied manifest: once it carries no WhatsApp
numbers (Disconnect clears them; switching WhatsApp off keeps them), the local session is
deleted and nothing is uploaded. The logout command and the loop share a lock, so an upload in
flight cannot land after the logout deleted the cloud copy.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

import structlog

from sellerclaw_agent.async_backoff import capped_exponential_backoff, sleep_until

_log = structlog.get_logger(__name__)

WHATSAPP_SESSION_REL_DIR: Final[str] = "credentials/whatsapp"
_CREDS_FILE: Final[str] = "creds.json"

POLL_SECONDS: Final[float] = 15.0
QUIET_SECONDS: Final[float] = 60.0
MIN_INTERVAL_SECONDS: Final[float] = 300.0
MAX_STALENESS_SECONDS: Final[float] = 900.0
FAILURE_BACKOFF_SECONDS: Final[float] = 60.0
MAX_FAILURE_BACKOFF_SECONDS: Final[float] = 900.0

_FILE_MODE: Final[int] = 0o600
_DIR_MODE: Final[int] = 0o700


class SessionSnapshotUnstableError(RuntimeError):
    """A session file did not parse — most likely caught mid-write. Retry later."""


class WhatsappSessionCloud(Protocol):
    async def upload_whatsapp_session(self, archive: bytes) -> None: ...


def whatsapp_session_dir(state_dir: Path) -> Path:
    return state_dir / WHATSAPP_SESSION_REL_DIR


def whatsapp_disconnected_in(manifest: Mapping[str, Any] | None) -> bool:
    """True when the manifest says the owner has no WhatsApp set up (no allowed numbers).

    Disconnect clears the numbers; switching WhatsApp off keeps them, and with them the link.
    No manifest yet (an unpaired agent) says nothing either way.
    """
    if not manifest:
        return False
    channels = manifest.get("channels")
    whatsapp = channels.get("whatsapp") if isinstance(channels, Mapping) else None
    return isinstance(whatsapp, Mapping) and not whatsapp.get("allowed_numbers")


def has_local_whatsapp_session(state_dir: Path) -> bool:
    """True when any WhatsApp account directory holds credentials."""
    root = whatsapp_session_dir(state_dir)
    if not root.is_dir():
        return False
    return any(path.is_file() for path in root.glob(f"*/{_CREDS_FILE}"))


def _session_files(state_dir: Path) -> list[Path]:
    root = whatsapp_session_dir(state_dir)
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def session_fingerprint(state_dir: Path) -> tuple[tuple[str, int, int], ...] | None:
    """Cheap change detector: (path, size, mtime) of every session file; None without one."""
    files = _session_files(state_dir)
    if not files:
        return None
    out: list[tuple[str, int, int]] = []
    for path in files:
        try:
            info = path.stat()
        except OSError:
            continue
        out.append((path.relative_to(state_dir).as_posix(), info.st_size, info.st_mtime_ns))
    return tuple(out) or None


def build_whatsapp_session_archive(state_dir: Path) -> bytes | None:
    """A gzip tar of the session directory, or None when there is no linked account.

    Raises :class:`SessionSnapshotUnstableError` when a ``*.json`` file does not parse or an
    account directory has no readable ``creds.json`` — the copy would not restore a session.
    """
    if not has_local_whatsapp_session(state_dir):
        return None
    contents: list[tuple[str, bytes]] = []
    for path in _session_files(state_dir):
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise SessionSnapshotUnstableError(f"cannot read {path.name}: {exc}") from exc
        if path.suffix == ".json" or path.name.endswith(".json.bak"):
            try:
                json.loads(data)
            except ValueError as exc:
                raise SessionSnapshotUnstableError(f"{path.name} does not parse") from exc
        contents.append((path.relative_to(state_dir).as_posix(), data))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for arcname, data in contents:
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mode = _FILE_MODE
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def restore_whatsapp_session(state_dir: Path, archive: bytes) -> int:
    """Put the session files from ``archive`` in place under ``state_dir``; return how many.

    Only regular files under the WhatsApp session directory are accepted; anything else in the
    archive is ignored, and a path that escapes it is refused outright. Every entry is checked
    before anything is written, and the files land in a scratch directory that replaces the
    session directory in one rename: a restore cut short (full disk, a kill) must not leave a
    ``creds.json`` without its keys, which would pass for a session and stop the next start
    from restoring again.
    """
    root = whatsapp_session_dir(state_dir)
    resolved_root = root.resolve()
    files: list[tuple[Path, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            dest = (state_dir / member.name).resolve()
            try:
                relative = dest.relative_to(resolved_root)
            except ValueError as exc:
                raise ValueError(f"Unsafe path in WhatsApp session archive: {member.name!r}") from exc
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            files.append((relative, extracted.read()))
    root.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".whatsapp-restore-", dir=root.parent))  # 0700
    try:
        for relative, data in files:
            dest = scratch / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(dest.parent, _DIR_MODE)
            dest.write_bytes(data)
            os.chmod(dest, _FILE_MODE)
        if root.exists():
            shutil.rmtree(root)  # leftovers without creds.json — that is why we restore
        os.replace(scratch, root)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return len(files)


def remove_whatsapp_session(state_dir: Path) -> bool:
    """Delete the local session directory; True when there was one."""
    root = whatsapp_session_dir(state_dir)
    if not root.exists():
        return False
    # Everything in it is owner-writable (Baileys writes 0600/0644, a restore writes 0600).
    shutil.rmtree(root)
    return True


@dataclass
class WhatsappSessionBackupLoop:
    """Uploads the session after it settles; never more often than the interval allows."""

    state_dir: Path
    cloud: WhatsappSessionCloud
    #: True once the applied manifest says the owner disconnected WhatsApp.
    owner_disconnected: Callable[[], bool] = field(default=lambda: False)
    poll_seconds: float = POLL_SECONDS
    quiet_seconds: float = QUIET_SECONDS
    min_interval_seconds: float = MIN_INTERVAL_SECONDS
    max_staleness_seconds: float = MAX_STALENESS_SECONDS
    clock: Callable[[], float] = field(default=time.monotonic)

    _uploaded: tuple[tuple[str, int, int], ...] | None = field(default=None, init=False)
    _seen: tuple[tuple[str, int, int], ...] | None = field(default=None, init=False)
    _changed_at: float = field(default=0.0, init=False)
    _uploaded_at: float | None = field(default=None, init=False)
    _first_pending_at: float | None = field(default=None, init=False)
    _urgent: bool = field(default=False, init=False)
    _retry_at: float | None = field(default=None, init=False)
    _failures: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def _now(self) -> float:
        return self.clock()

    def request_upload_soon(self) -> None:
        """A link just completed: skip the interval, still wait for the files to settle."""
        self._urgent = True

    def forget_uploaded(self) -> None:
        """The cloud copy was deleted (logout) — the next session is new, not a duplicate."""
        self._uploaded = None
        self._uploaded_at = None
        self._first_pending_at = None
        self._urgent = False
        self._retry_at = None
        self._failures = 0

    @asynccontextmanager
    async def paused(self) -> AsyncIterator[None]:
        """Hold uploads off (logout): one already under way finishes first."""
        async with self._lock:
            yield

    async def upload_now(self) -> bool:
        """Copy the session at once, settled or not — OpenClaw just stopped, nothing writes."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            if await loop.run_in_executor(None, self.owner_disconnected):
                return False
            fingerprint = await loop.run_in_executor(None, session_fingerprint, self.state_dir)
            if fingerprint is None or fingerprint == self._uploaded:
                return False
            return await self._upload(fingerprint, self._now())

    async def tick(self) -> bool:
        """Check once; upload when due. Returns True when an upload happened."""
        async with self._lock:
            return await self._tick()

    async def _tick(self) -> bool:
        loop = asyncio.get_running_loop()
        if await loop.run_in_executor(None, self.owner_disconnected):  # reads the saved manifest
            removed = await loop.run_in_executor(None, remove_whatsapp_session, self.state_dir)
            if removed:
                _log.info("whatsapp_session_removed_after_disconnect")
                self.forget_uploaded()
            self._seen = None
            return False
        fingerprint = await loop.run_in_executor(None, session_fingerprint, self.state_dir)
        now = self._now()
        if fingerprint is None or fingerprint == self._uploaded:
            self._seen = fingerprint
            self._first_pending_at = None
            return False
        if fingerprint != self._seen:
            self._seen = fingerprint
            self._changed_at = now
            if self._first_pending_at is None:
                self._first_pending_at = now
        quiet = (now - self._changed_at) >= self.quiet_seconds
        interval_ok = (
            self._urgent
            or self._uploaded_at is None
            or (now - self._uploaded_at) >= self.min_interval_seconds
        )
        stale = (
            self._first_pending_at is not None
            and (now - self._first_pending_at) >= self.max_staleness_seconds
        )
        if not ((quiet and interval_ok) or stale):
            return False
        if self._retry_at is not None and now < self._retry_at:
            return False
        return await self._upload(fingerprint, now)

    async def _upload(self, fingerprint: tuple[tuple[str, int, int], ...], now: float) -> bool:
        loop = asyncio.get_running_loop()
        try:
            archive = await loop.run_in_executor(
                None, build_whatsapp_session_archive, self.state_dir
            )
        except SessionSnapshotUnstableError as exc:
            _log.info("whatsapp_session_snapshot_unstable", error=str(exc)[:200])
            return False
        if archive is None:
            return False
        try:
            await self.cloud.upload_whatsapp_session(archive)
        except Exception as exc:  # noqa: BLE001 - retried after a growing pause
            self._failures += 1
            pause = capped_exponential_backoff(
                FAILURE_BACKOFF_SECONDS, self._failures, MAX_FAILURE_BACKOFF_SECONDS
            )
            self._retry_at = now + pause
            _log.warning(
                "whatsapp_session_upload_failed", error=str(exc)[:300], retry_in_seconds=pause
            )
            return False
        self._uploaded = fingerprint
        self._uploaded_at = now
        self._first_pending_at = None
        self._urgent = False
        self._retry_at = None
        self._failures = 0
        _log.info("whatsapp_session_uploaded", bytes=len(archive))
        return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.tick()
            await sleep_until(stop, self.poll_seconds)


_loop: WhatsappSessionBackupLoop | None = None


def install_whatsapp_session_backup_loop(loop: WhatsappSessionBackupLoop | None) -> None:
    global _loop
    _loop = loop


def get_whatsapp_session_backup_loop() -> WhatsappSessionBackupLoop | None:
    return _loop
