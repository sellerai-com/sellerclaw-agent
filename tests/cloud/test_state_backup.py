from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from sellerclaw_agent.cloud.state_backup import (
    build_state_backup_archive,
    iter_state_backup_files,
    read_applied_config_version,
    restore_state_backup,
    state_dir_has_restoreable_data,
    write_applied_config_version,
)

pytestmark = pytest.mark.unit


def test_write_then_read_applied_config_version_roundtrips(tmp_path: Path) -> None:
    # Version lives in a SellerClaw sidecar file, NOT in openclaw.json (OpenClaw's meta is a
    # closed schema and rejects unknown keys).
    write_applied_config_version(42, tmp_path)
    assert not (tmp_path / "openclaw.json").exists()
    assert read_applied_config_version(tmp_path) == 42
    # Monotonic on the cloud side, but the file itself just reflects the last write.
    write_applied_config_version(43, tmp_path)
    assert read_applied_config_version(tmp_path) == 43


def test_read_applied_config_version_none_when_missing_or_malformed(tmp_path: Path) -> None:
    assert read_applied_config_version(tmp_path) is None
    (tmp_path / "sellerclaw-config-version").write_text("not-an-int", encoding="utf-8")
    assert read_applied_config_version(tmp_path) is None


def _write_tree(base: Path) -> None:
    for rel, content in (
        ("agents/sup/agent/openclaw-agent.sqlite", b"SQLite format 3\x00"),
        ("workspace-w1/MEMORY.md", b"# m"),
        ("workspace-w1/memory/chunk.md", b"c"),
        ("browser/openclaw/user-data/Local State", b"{}"),
        ("browser/openclaw/user-data/Default/Cookies", b"sqlite"),
        ("browser/openclaw/user-data/Default/Cache/data_0", b"junk"),
        ("chrome-profile/Default/Cookies", b"sqlite"),
    ):
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        # Chats live in the SellerClaw database, so the transcript store never travels.
        pytest.param("agents/sup/agent/openclaw-agent.sqlite", False, id="session-store"),
        pytest.param("agents/sup/agent/openclaw-agent.sqlite-wal", False, id="session-store-wal"),
        # Durable facts live in cloud-side long-term memory, not workspace files.
        pytest.param("workspace-w1/MEMORY.md", False, id="workspace-memory-md"),
        pytest.param("workspace-w1/memory/chunk.md", False, id="workspace-memory-dir"),
        # Sign-in state from the profile the browser plugin actually drives.
        pytest.param("browser/openclaw/user-data/Local State", True, id="browser-local-state"),
        pytest.param("browser/openclaw/user-data/Default/Cookies", True, id="browser-cookies"),
        pytest.param("browser/openclaw/user-data/Default/Login Data", True, id="browser-login-data"),
        pytest.param(
            "browser/openclaw/user-data/Default/Local Storage/leveldb/000003.log",
            True,
            id="browser-local-storage",
        ),
        # Cache must never travel — it is the bulk of a real profile.
        pytest.param("browser/openclaw/user-data/Default/Cache/data_0", False, id="browser-cache"),
        pytest.param(
            "browser/openclaw/user-data/optimization_guide_model_store/m.bin",
            False,
            id="browser-model-store",
        ),
        # Legacy fallback profile, used when Chrome starts without --user-data-dir.
        pytest.param("chrome-profile/Default/Cookies", True, id="legacy-profile-cookies"),
        pytest.param("chrome-profile/Default/Cache/data_0", False, id="legacy-profile-cache"),
    ],
)
def test_state_backup_allowlist(tmp_path: Path, rel: str, expected: bool) -> None:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    included = {p.relative_to(tmp_path).as_posix() for p in iter_state_backup_files(tmp_path)}

    assert (rel in included) is expected


def test_iter_state_backup_files_collects_browser_logins_only(tmp_path: Path) -> None:
    _write_tree(tmp_path)

    rels = {p.relative_to(tmp_path).as_posix() for p in iter_state_backup_files(tmp_path)}

    assert rels == {
        "browser/openclaw/user-data/Local State",
        "browser/openclaw/user-data/Default/Cookies",
        "chrome-profile/Default/Cookies",
    }


def test_build_and_restore_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write_tree(src)

    archive = build_state_backup_archive(src)
    assert archive is not None
    assert archive[:2] == b"\x1f\x8b"
    restore_state_backup(dst, archive)

    assert (dst / "browser" / "openclaw" / "user-data" / "Default" / "Cookies").exists()
    assert (dst / "browser" / "openclaw" / "user-data" / "Local State").read_text() == "{}"
    assert (dst / "chrome-profile" / "Default" / "Cookies").exists()
    assert not (dst / "agents" / "sup" / "agent" / "openclaw-agent.sqlite").exists()
    assert not (dst / "workspace-w1" / "MEMORY.md").exists()
    assert not (dst / "browser" / "openclaw" / "user-data" / "Default" / "Cache").exists()


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        pytest.param("browser/openclaw/user-data/Default/Cookies", True, id="browser-profile"),
        pytest.param("chrome-profile/Default/Cookies", True, id="legacy-browser-profile"),
        # A machine that only has chats and memory still needs its browser logins back.
        pytest.param("agents/a/agent/openclaw-agent.sqlite", False, id="session-store"),
        pytest.param("workspace-w1/MEMORY.md", False, id="workspace-memory-md"),
        pytest.param("browser/openclaw/user-data/Default/Cache/data_0", False, id="browser-cache"),
    ],
)
def test_state_dir_has_restoreable_data(tmp_path: Path, rel: str, expected: bool) -> None:
    assert state_dir_has_restoreable_data(tmp_path) is False
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")

    assert state_dir_has_restoreable_data(tmp_path) is expected


def test_build_archive_returns_none_when_nothing_to_back_up(tmp_path: Path) -> None:
    """The cloud keeps one archive per user; an empty tar would overwrite the good one."""
    (tmp_path / "agents" / "sup" / "agent").mkdir(parents=True)
    (tmp_path / "agents" / "sup" / "agent" / "openclaw-agent.sqlite").write_bytes(b"db")

    assert build_state_backup_archive(tmp_path) is None
    assert build_state_backup_archive(tmp_path / "missing") is None


def test_restore_rejects_path_traversal(tmp_path: Path) -> None:
    state_dir = tmp_path / "st"
    state_dir.mkdir()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        raw = b"evil"
        info = tarfile.TarInfo(name="../outside.txt")
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
    with pytest.raises(ValueError, match="Unsafe path"):
        restore_state_backup(state_dir, buffer.getvalue())
