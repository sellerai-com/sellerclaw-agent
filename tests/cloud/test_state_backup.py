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
        ("agents/sup/sessions/chat.jsonl", b"{}\n"),
        ("agents/sup/sessions/sessions.json", b'{"key":"id"}'),
        ("agents/sup/sessions/chat.jsonl.lock", b""),
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
        pytest.param("agents/sup/sessions/chat.jsonl", True, id="session-transcript"),
        # Without the index the transcripts cannot be matched back to a session key.
        pytest.param("agents/sup/sessions/sessions.json", True, id="session-index"),
        pytest.param("agents/sup/sessions/chat.jsonl.lock", False, id="session-lock"),
        pytest.param("agents/sup/sessions/notes.txt", False, id="session-unrelated-file"),
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

    included = {
        p.relative_to(tmp_path).as_posix()
        for p in iter_state_backup_files(tmp_path, include_browser_profile=True)
    }

    assert (rel in included) is expected


def test_iter_state_backup_files_without_browser_profile(tmp_path: Path) -> None:
    _write_tree(tmp_path)

    rels = {
        p.relative_to(tmp_path).as_posix()
        for p in iter_state_backup_files(tmp_path, include_browser_profile=False)
    }

    assert rels == {
        "agents/sup/sessions/chat.jsonl",
        "agents/sup/sessions/sessions.json",
    }


def test_build_and_restore_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write_tree(src)

    archive = build_state_backup_archive(src, include_browser_profile=True)
    assert archive[:2] == b"\x1f\x8b"
    restore_state_backup(dst, archive)

    assert (dst / "agents" / "sup" / "sessions" / "chat.jsonl").read_text() == "{}\n"
    assert (dst / "agents" / "sup" / "sessions" / "sessions.json").read_text() == '{"key":"id"}'
    assert (dst / "browser" / "openclaw" / "user-data" / "Default" / "Cookies").exists()
    assert not (dst / "workspace-w1" / "MEMORY.md").exists()
    assert not (dst / "browser" / "openclaw" / "user-data" / "Default" / "Cache").exists()


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        pytest.param("agents/a/sessions/x.jsonl", True, id="session-transcript"),
        pytest.param("agents/a/sessions/sessions.json", True, id="session-index"),
        # Chrome creates a profile on first launch, so it must not read as "state exists"
        # — that would make a cold start skip the restore it needs.
        pytest.param("browser/openclaw/user-data/Default/Cookies", False, id="browser-profile"),
        pytest.param("workspace-w1/MEMORY.md", False, id="workspace-memory-md"),
    ],
)
def test_state_dir_has_restoreable_data(tmp_path: Path, rel: str, expected: bool) -> None:
    assert state_dir_has_restoreable_data(tmp_path) is False
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")

    assert state_dir_has_restoreable_data(tmp_path) is expected


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
