from __future__ import annotations

import pytest
from sellerclaw_agent.server.storage import ManifestStorage

pytestmark = pytest.mark.unit


def test_manifest_storage_save_and_load_roundtrip(tmp_path) -> None:
    storage = ManifestStorage(tmp_path)
    data = {"user_id": "u1", "nested": {"a": 1}, "list": [1, 2]}
    path, version = storage.save(data)
    assert path == tmp_path / "manifest.json"
    assert len(version) == 16
    loaded = storage.load()
    assert loaded == data


def test_manifest_storage_load_missing_returns_none(tmp_path) -> None:
    storage = ManifestStorage(tmp_path)
    assert storage.load() is None


def test_manifest_storage_overwrite(tmp_path) -> None:
    storage = ManifestStorage(tmp_path)
    storage.save({"v": 1})
    storage.save({"v": 2})
    assert storage.load() == {"v": 2}


def test_manifest_storage_load_rejects_non_object_root(tmp_path) -> None:
    storage = ManifestStorage(tmp_path)
    storage.manifest_path.write_text("[1,2]", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be an object"):
        storage.load()
