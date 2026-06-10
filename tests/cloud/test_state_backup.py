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
    (base / "agents" / "sup" / "sessions").mkdir(parents=True)
    (base / "agents" / "sup" / "sessions" / "chat.jsonl").write_text("{}\n", encoding="utf-8")
    (base / "agents" / "sup" / "sessions" / "chat.jsonl.lock").write_text("", encoding="utf-8")
    (base / "workspace-w1" / "memory").mkdir(parents=True)
    (base / "workspace-w1" / "MEMORY.md").write_text("# m", encoding="utf-8")
    (base / "workspace-w1" / "memory" / "chunk.md").write_text("c", encoding="utf-8")
    (base / "chrome-profile" / "Default").mkdir(parents=True)
    (base / "chrome-profile" / "Default" / "prefs").write_bytes(b"{}")


def test_iter_state_backup_files_respects_chrome_flag(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    light = iter_state_backup_files(tmp_path, include_chrome=False)
    rels = {p.relative_to(tmp_path).as_posix() for p in light}
    assert rels == {
        "agents/sup/sessions/chat.jsonl",
        "workspace-w1/MEMORY.md",
        "workspace-w1/memory/chunk.md",
    }
    full = iter_state_backup_files(tmp_path, include_chrome=True)
    rels_full = {p.relative_to(tmp_path).as_posix() for p in full}
    assert "chrome-profile/Default/prefs" in rels_full
    assert "agents/sup/sessions/chat.jsonl.lock" not in rels_full


def test_build_and_restore_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write_tree(src)
    archive = build_state_backup_archive(src, include_chrome=False)
    assert archive[:2] == b"\x1f\x8b"
    restore_state_backup(dst, archive)
    assert (dst / "agents" / "sup" / "sessions" / "chat.jsonl").read_text() == "{}\n"
    assert (dst / "workspace-w1" / "MEMORY.md").read_text() == "# m"


def test_state_dir_has_restoreable_data(tmp_path: Path) -> None:
    assert state_dir_has_restoreable_data(tmp_path) is False
    p = tmp_path / "agents" / "a" / "sessions" / "x.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("x", encoding="utf-8")
    assert state_dir_has_restoreable_data(tmp_path) is True


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
