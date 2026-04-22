"""Local secrets persisted under ``SELLERCLAW_DATA_DIR/secrets.json`` (mode 0600)."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SECRETS_FILENAME = "secrets.json"
_LEGACY_LOCAL_KEY_FILE = "local_api_key"

_KEY_ORDER = ("local_api_key", "gateway_token", "hooks_token")
_ENV_FOR_KEY = {
    "local_api_key": "SELLERCLAW_LOCAL_API_KEY",
    "gateway_token": "SELLERCLAW_GATEWAY_TOKEN",
    "hooks_token": "SELLERCLAW_HOOKS_TOKEN",
}


@dataclass(frozen=True)
class LocalSecrets:
    local_api_key: str
    gateway_token: str
    hooks_token: str


_CACHE: LocalSecrets | None = None
_CACHED_DIR: Path | None = None


def reset_secrets_cache() -> None:
    """Clear cached secrets (for tests)."""
    global _CACHE, _CACHED_DIR
    _CACHE = None
    _CACHED_DIR = None


def _env_for(key: str) -> str:
    return (os.environ.get(_ENV_FOR_KEY[key]) or "").strip()


def _read_secrets_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k in _KEY_ORDER:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def _atomic_write_json(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(tmp_path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
    os.replace(tmp_path, path)


def migrate_legacy_manifest_tokens_into_secrets(
    data_dir: Path, manifest: dict[str, Any]
) -> dict[str, Any] | None:
    """Copy ``gateway_token`` / ``hooks_token`` from an on-disk manifest into ``secrets.json`` when missing.

    Returns a manifest dict with those keys removed (for rewriting ``manifest.json``), or ``None`` when the
    manifest had no such keys.
    """
    if "gateway_token" not in manifest and "hooks_token" not in manifest:
        return None

    path = data_dir / _SECRETS_FILENAME
    file_map = _read_secrets_file(path)
    updates: dict[str, str] = {}
    for key in ("gateway_token", "hooks_token"):
        raw = manifest.get(key)
        val = raw.strip() if isinstance(raw, str) else ""
        if not val or _env_for(key):
            continue
        if key not in file_map:
            updates[key] = val

    if updates:
        merged = {**file_map, **updates}
        for key in _KEY_ORDER:
            if key in merged:
                continue
            if key == "local_api_key":
                legacy = data_dir / _LEGACY_LOCAL_KEY_FILE
                if legacy.is_file():
                    leg = legacy.read_text(encoding="utf-8").strip()
                    if leg:
                        merged[key] = leg
                        continue
            merged[key] = secrets.token_urlsafe(32)
        write_map = {k: merged[k] for k in _KEY_ORDER if not _env_for(k)}
        if write_map:
            _atomic_write_json(path, write_map)

    return {k: v for k, v in manifest.items() if k not in ("gateway_token", "hooks_token")}


def load_or_create_secrets(data_dir: Path) -> LocalSecrets:
    """Load secrets from env, ``secrets.json``, or generate; migrate legacy ``local_api_key`` file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / _SECRETS_FILENAME
    legacy = data_dir / _LEGACY_LOCAL_KEY_FILE

    file_map = _read_secrets_file(path)
    migrated = False
    legacy_local_key: str | None = None
    if not file_map and legacy.is_file():
        leg = legacy.read_text(encoding="utf-8").strip()
        if leg:
            file_map = {"local_api_key": leg}
            legacy_local_key = leg
        migrated = True

    resolved: dict[str, str] = {}
    missing_disk = False
    for key in _KEY_ORDER:
        if _env_for(key):
            resolved[key] = _env_for(key)
            continue
        if key in file_map:
            resolved[key] = file_map[key]
        else:
            resolved[key] = secrets.token_urlsafe(32)
            missing_disk = True

    write_map = {k: resolved[k] for k in _KEY_ORDER if not _env_for(k)}
    should_write = bool(write_map) and (migrated or not path.is_file() or missing_disk)
    if should_write:
        _atomic_write_json(path, write_map)
    if (
        migrated
        and legacy_local_key is not None
        and not _env_for("local_api_key")
        and write_map.get("local_api_key") == legacy_local_key
    ):
        with contextlib.suppress(OSError):
            legacy.unlink()

    return LocalSecrets(
        local_api_key=resolved["local_api_key"],
        gateway_token=resolved["gateway_token"],
        hooks_token=resolved["hooks_token"],
    )


def get_secrets(data_dir: Path) -> LocalSecrets:
    """Return cached secrets for this data directory (resolved once per process)."""
    global _CACHE, _CACHED_DIR
    resolved_dir = data_dir.resolve()
    if _CACHE is not None and _CACHED_DIR == resolved_dir:
        return _CACHE
    _CACHE = load_or_create_secrets(data_dir)
    _CACHED_DIR = resolved_dir
    return _CACHE
