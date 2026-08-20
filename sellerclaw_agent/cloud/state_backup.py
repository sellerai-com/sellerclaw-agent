from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path


# Chrome scatters sign-in state across a handful of small files; the rest of a profile is
# cache — 115 MB of the 127 MB measured on a real agent — and must never travel. Two roots
# are covered: the profile the browser plugin actually drives, and the legacy fallback used
# when Chrome is launched without an explicit ``--user-data-dir``.
_BROWSER_PROFILE_ROOTS = ("browser/openclaw/user-data", "chrome-profile")
_BROWSER_PROFILE_ROOT_FILES = frozenset({"Local State"})
_BROWSER_PROFILE_DEFAULT_FILES = frozenset({"Cookies", "Login Data", "Preferences"})
_BROWSER_PROFILE_DEFAULT_DIRS = ("Local Storage", "Session Storage")


def _is_browser_login_state(path: Path, state_dir: Path) -> bool:
    """True for the profile files that carry browser sign-in state.

    ``Local State`` is included because it holds the key Chrome encrypts cookies with —
    restore the cookie database without it and the values cannot be decrypted.
    """
    try:
        rel = path.relative_to(state_dir).as_posix()
    except ValueError:
        return False
    for root in _BROWSER_PROFILE_ROOTS:
        prefix = f"{root}/"
        if not rel.startswith(prefix):
            continue
        tail = rel[len(prefix) :]
        if tail in _BROWSER_PROFILE_ROOT_FILES:
            return True
        if not tail.startswith("Default/"):
            return False
        inner = tail[len("Default/") :]
        if inner in _BROWSER_PROFILE_DEFAULT_FILES:
            return True
        return any(inner.startswith(f"{name}/") for name in _BROWSER_PROFILE_DEFAULT_DIRS)
    return False


def iter_state_backup_files(state_dir: Path) -> list[Path]:
    """List files to include in an edge state backup: browser sign-in state only.

    Everything else the agent keeps on disk is reproducible from the cloud. Chats live in
    SellerClaw's database, durable facts in long-term memory, and configs arrive in the
    bundle on every start — so restoring any of them from a tar would at best duplicate
    what the cloud already holds and at worst resurrect a stale copy of it. Browser logins
    are the one exception: nothing outside this machine knows them.
    """
    if not state_dir.is_dir():
        return []
    out: list[Path] = []
    for path in state_dir.rglob("*"):
        if not path.is_file():
            continue
        if _is_browser_login_state(path, state_dir):
            out.append(path)
    return sorted(out)


def build_state_backup_archive(state_dir: Path) -> bytes | None:
    """Build a gzip tar of allowlisted paths, or ``None`` when there is nothing to back up.

    The cloud keeps exactly one archive per user (``latest.tar.gz``), so an empty tar is
    not a harmless no-op: uploaded, it would overwrite the archive that still holds the
    browser sign-ins — and "no sign-in files on disk yet" is the normal condition of a
    freshly recreated machine whose restore has not run or whose Chrome has not launched.
    """
    paths = iter_state_backup_files(state_dir)
    if not paths:
        return None
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for file_path in paths:
            arcname = file_path.relative_to(state_dir).as_posix()
            archive.add(file_path, arcname=arcname, recursive=False)
    return buffer.getvalue()


def restore_state_backup(state_dir: Path, archive: bytes) -> None:
    """Extract a gzip tar produced by :func:`build_state_backup_archive` into ``state_dir``."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            dest = (state_dir / member.name).resolve()
            try:
                dest.relative_to(state_dir.resolve())
            except ValueError as exc:
                raise ValueError(f"Unsafe path in state backup archive: {member.name!r}") from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            dest.write_bytes(extracted.read())


def state_dir_has_restoreable_data(state_dir: Path) -> bool:
    """Return True if local state already carries browser sign-in files (skip cloud restore).

    The restore runs from the entrypoint before Chrome has ever launched, so on a genuine
    cold start none of these files exist yet and the download proceeds. Once Chrome has run,
    the profile on disk is newer than any backup and must win.
    """
    if not state_dir.is_dir():
        return False
    for path in state_dir.rglob("*"):
        if not path.is_file():
            continue
        if _is_browser_login_state(path, state_dir):
            return True
    return False


def default_openclaw_state_dir() -> Path:
    return Path(os.environ.get("OPENCLAW_STATE_DIR", "/home/node/.openclaw"))


# Applied manifest config_version is kept in a SellerClaw-owned sidecar file, NOT inside
# openclaw.json: OpenClaw validates its `meta` block with a closed schema and rejects unknown
# keys ("meta: Invalid input"), refusing to start. The agent writes this file right after it
# applies a bundle and reports its contents in every ping.
_CONFIG_VERSION_FILENAME = "sellerclaw-config-version"


def applied_config_version_path(state_dir: Path | None = None) -> Path:
    return (state_dir or default_openclaw_state_dir()) / _CONFIG_VERSION_FILENAME


def write_applied_config_version(version: int, state_dir: Path | None = None) -> None:
    """Persist the config_version the agent just applied (atomic replace).

    Best-effort: callers invoke this after a successful bundle apply; a write failure must
    not fail the apply (the cloud just won't see the new version until the next successful
    write and re-applies — self-healing).
    """
    path = applied_config_version_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(str(int(version)), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def read_applied_config_version(state_dir: Path | None = None) -> int | None:
    """Read the config_version the agent last applied (from the sidecar state file).

    Returns ``None`` when the file is missing/unreadable/malformed — the cloud treats a
    missing value as "unknown" and the ping simply omits ``config_version`` (no reconcile).
    This is what the edge agent reports in every ping so the cloud can detect an undelivered
    manifest.
    """
    path = applied_config_version_path(state_dir)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        version = int(raw)
    except ValueError:
        return None
    return version if version >= 0 else None
