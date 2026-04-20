from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path


def _is_session_jsonl(path: Path, state_dir: Path) -> bool:
    try:
        rel = path.relative_to(state_dir).as_posix()
    except ValueError:
        return False
    parts = rel.split("/")
    if len(parts) != 4:
        return False
    if parts[0] != "agents" or parts[2] != "sessions":
        return False
    return path.suffix == ".jsonl" and not path.name.endswith(".lock")


def _is_workspace_memory_md(path: Path, state_dir: Path) -> bool:
    try:
        rel = path.relative_to(state_dir).as_posix()
    except ValueError:
        return False
    parts = rel.split("/")
    if not parts or not parts[0].startswith("workspace-"):
        return False
    return len(parts) == 2 and parts[1] == "MEMORY.md"


def _is_under_workspace_memory_dir(path: Path, state_dir: Path) -> bool:
    try:
        rel = path.relative_to(state_dir).as_posix()
    except ValueError:
        return False
    parts = rel.split("/")
    return (
        len(parts) >= 3
        and parts[0].startswith("workspace-")
        and parts[1] == "memory"
        and path.is_file()
    )


def _is_under_chrome_profile(path: Path, state_dir: Path) -> bool:
    try:
        rel = path.relative_to(state_dir).as_posix()
    except ValueError:
        return False
    parts = rel.split("/")
    return len(parts) >= 1 and parts[0] == "chrome-profile" and path.is_file()


def iter_state_backup_files(state_dir: Path, *, include_chrome: bool) -> list[Path]:
    """List files to include in an edge state backup (OpenClaw + optional Chrome profile)."""
    if not state_dir.is_dir():
        return []
    out: list[Path] = []
    for path in state_dir.rglob("*"):
        if not path.is_file():
            continue
        if _is_session_jsonl(path, state_dir):
            out.append(path)
        elif _is_workspace_memory_md(path, state_dir):
            out.append(path)
        elif _is_under_workspace_memory_dir(path, state_dir):
            out.append(path)
        elif include_chrome and _is_under_chrome_profile(path, state_dir):
            out.append(path)
    return sorted(out)


def build_state_backup_archive(state_dir: Path, *, include_chrome: bool) -> bytes:
    """Build a gzip-compressed tar of allowlisted paths under ``state_dir``."""
    paths = iter_state_backup_files(state_dir, include_chrome=include_chrome)
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
    """Return True if local state already has sessions or MEMORY files (skip cloud restore)."""
    if not state_dir.is_dir():
        return False
    for path in state_dir.rglob("*"):
        if not path.is_file():
            continue
        if _is_session_jsonl(path, state_dir) or _is_workspace_memory_md(path, state_dir):
            return True
    return False


def default_openclaw_state_dir() -> Path:
    return Path(os.environ.get("OPENCLAW_STATE_DIR", "/home/node/.openclaw"))
