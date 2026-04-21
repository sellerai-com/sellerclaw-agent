from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import sellerclaw_agent.server.media_upload as media_upload
from sellerclaw_agent.server.storage import ManifestStorage

pytestmark = pytest.mark.unit


def _seed_manifest(data_dir: Path, hooks_token: str = "hooks-secret") -> None:
    ManifestStorage(data_dir).save(
        {
            "user_id": "11111111-1111-4111-8111-111111111111",
            "hooks_token": hooks_token,
        }
    )


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def allowed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        media_upload,
        "ALLOWED_PATH_PREFIXES",
        (str(tmp_path) + "/",),
    )
    f = tmp_path / "shot.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n-fake-")
    return f


@pytest.fixture()
def app_client(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    app = FastAPI()
    app.include_router(media_upload.router)
    yield TestClient(app)


@pytest.fixture()
def stub_cloud(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    stub = MagicMock()

    async def _fake_proxy(
        *, content: bytes, filename: str, content_type: str, bearer: str
    ) -> dict[str, Any]:
        stub(content=content, filename=filename, content_type=content_type, bearer=bearer)
        return {
            "file_id": "fid-123",
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(content),
            "download_url": "https://cloud.example/files/fid-123/shot.png",
            "expires_at": "2099-01-01T00:00:00Z",
        }

    monkeypatch.setattr(media_upload, "_proxy_to_cloud", _fake_proxy)
    return stub


@pytest.fixture()
def stub_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_upload,
        "resolve_agent_bearer_token_from_data_dir",
        lambda _dir: "sca_unit_test_token",
    )


class TestRequireHooksToken:
    def test_rejects_missing_header(self, data_dir: Path) -> None:
        _seed_manifest(data_dir)
        with pytest.raises(HTTPException) as exc:
            media_upload.require_hooks_token(authorization=None)
        assert exc.value.status_code == 401

    def test_rejects_non_bearer(self, data_dir: Path) -> None:
        _seed_manifest(data_dir)
        with pytest.raises(HTTPException) as exc:
            media_upload.require_hooks_token(authorization="Basic abcd")
        assert exc.value.status_code == 401

    def test_rejects_mismatched_token(self, data_dir: Path) -> None:
        _seed_manifest(data_dir, hooks_token="expected")
        with pytest.raises(HTTPException) as exc:
            media_upload.require_hooks_token(authorization="Bearer wrong")
        assert exc.value.status_code == 401

    def test_accepts_matching_token(self, data_dir: Path) -> None:
        _seed_manifest(data_dir, hooks_token="expected")
        media_upload.require_hooks_token(authorization="Bearer expected")

    def test_rejects_when_manifest_missing(self, data_dir: Path) -> None:
        with pytest.raises(HTTPException) as exc:
            media_upload.require_hooks_token(authorization="Bearer any")
        assert exc.value.status_code == 503
        assert exc.value.detail == "manifest_not_saved"

    def test_rejects_when_hooks_token_missing(self, data_dir: Path) -> None:
        ManifestStorage(data_dir).save({"user_id": "u"})
        with pytest.raises(HTTPException) as exc:
            media_upload.require_hooks_token(authorization="Bearer any")
        assert exc.value.status_code == 503
        assert exc.value.detail == "hooks_token_missing"


class TestValidateLocalPath:
    def test_rejects_empty_input(self) -> None:
        with pytest.raises(HTTPException) as exc:
            media_upload._validate_local_path("")
        assert exc.value.status_code == 400

    def test_rejects_nonexistent_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope-does-not-exist.png"
        with pytest.raises(HTTPException) as exc:
            media_upload._validate_local_path(str(missing))
        assert exc.value.status_code == 404

    def test_rejects_path_outside_allowed_prefixes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            media_upload, "ALLOWED_PATH_PREFIXES", ("/home/node/.openclaw/media/",)
        )
        f = tmp_path / "outside.png"
        f.write_bytes(b"x")
        with pytest.raises(HTTPException) as exc:
            media_upload._validate_local_path(str(f))
        assert exc.value.status_code == 403
        assert exc.value.detail == "path_not_allowed"

    def test_rejects_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            media_upload, "ALLOWED_PATH_PREFIXES", (str(tmp_path) + "/",)
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        with pytest.raises(HTTPException) as exc:
            media_upload._validate_local_path(str(sub))
        assert exc.value.status_code == 400

    def test_accepts_allowed_file(self, allowed_file: Path) -> None:
        resolved = media_upload._validate_local_path(str(allowed_file))
        assert resolved == allowed_file.resolve()


class TestValidateExtension:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            pytest.param("shot.png", ".png", id="png"),
            pytest.param("report.CSV", ".csv", id="case-insensitive"),
            pytest.param("doc.md", ".md", id="markdown"),
        ],
    )
    def test_allowed(self, filename: str, expected: str) -> None:
        assert media_upload._validate_extension(filename) == expected

    def test_rejects_disallowed(self) -> None:
        with pytest.raises(HTTPException) as exc:
            media_upload._validate_extension("bad.exe")
        assert exc.value.status_code == 415


class TestReadBounded:
    def test_reads_small_file(self, tmp_path: Path) -> None:
        f = tmp_path / "small.bin"
        f.write_bytes(b"hello")
        assert media_upload._read_bounded(f) == b"hello"

    def test_rejects_oversize(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(media_upload, "MAX_FILE_SIZE_BYTES", 4)
        f = tmp_path / "too-big.bin"
        f.write_bytes(b"12345")
        with pytest.raises(HTTPException) as exc:
            media_upload._read_bounded(f)
        assert exc.value.status_code == 413


class TestContentTypeFor:
    @pytest.mark.parametrize(
        ("ext", "expected"),
        [
            pytest.param(".png", "image/png", id="png"),
            pytest.param(".jpg", "image/jpeg", id="jpg"),
            pytest.param(".jpeg", "image/jpeg", id="jpeg"),
            pytest.param(".webp", "image/webp", id="webp"),
            pytest.param(".gif", "image/gif", id="gif"),
            pytest.param(".csv", "text/csv; charset=utf-8", id="csv"),
            pytest.param(".md", "text/markdown; charset=utf-8", id="md"),
            pytest.param(".json", "application/json", id="json"),
            pytest.param(".xyz", "application/octet-stream", id="unknown"),
        ],
    )
    def test_mapping(self, ext: str, expected: str) -> None:
        assert media_upload._content_type_for(ext) == expected


class TestUploadLocalEndpoint:
    def test_happy_path_proxies_and_returns_download_url(
        self,
        app_client: TestClient,
        data_dir: Path,
        allowed_file: Path,
        stub_bearer: None,
        stub_cloud: MagicMock,
    ) -> None:
        _seed_manifest(data_dir, hooks_token="secret")
        res = app_client.post(
            "/internal/openclaw/media/upload-local",
            headers={"Authorization": "Bearer secret"},
            json={"local_path": str(allowed_file)},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["download_url"] == "https://cloud.example/files/fid-123/shot.png"
        assert body["filename"] == "shot.png"
        assert body["content_type"] == "image/png"
        assert body["size_bytes"] == allowed_file.stat().st_size
        stub_cloud.assert_called_once()
        kwargs = stub_cloud.call_args.kwargs
        assert kwargs["bearer"] == "sca_unit_test_token"
        assert kwargs["filename"] == "shot.png"
        assert kwargs["content_type"] == "image/png"
        assert kwargs["content"] == allowed_file.read_bytes()

    def test_returns_401_on_bad_bearer(
        self,
        app_client: TestClient,
        data_dir: Path,
    ) -> None:
        _seed_manifest(data_dir, hooks_token="secret")
        res = app_client.post(
            "/internal/openclaw/media/upload-local",
            headers={"Authorization": "Bearer wrong"},
            json={"local_path": "/tmp/any"},
        )
        assert res.status_code == 401

    def test_returns_403_for_disallowed_prefix(
        self,
        app_client: TestClient,
        data_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_manifest(data_dir, hooks_token="secret")
        monkeypatch.setattr(
            media_upload, "ALLOWED_PATH_PREFIXES", ("/home/node/.openclaw/media/",)
        )
        f = tmp_path / "nope.png"
        f.write_bytes(b"x")
        res = app_client.post(
            "/internal/openclaw/media/upload-local",
            headers={"Authorization": "Bearer secret"},
            json={"local_path": str(f)},
        )
        assert res.status_code == 403
        assert res.json()["detail"] == "path_not_allowed"

    def test_returns_503_when_agent_bearer_missing(
        self,
        app_client: TestClient,
        data_dir: Path,
        allowed_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_manifest(data_dir, hooks_token="secret")
        monkeypatch.setattr(
            media_upload, "resolve_agent_bearer_token_from_data_dir", lambda _d: None
        )
        res = app_client.post(
            "/internal/openclaw/media/upload-local",
            headers={"Authorization": "Bearer secret"},
            json={"local_path": str(allowed_file)},
        )
        assert res.status_code == 503
        assert res.json()["detail"] == "agent_not_authenticated"

    def test_returns_415_for_bad_extension(
        self,
        app_client: TestClient,
        data_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_manifest(data_dir, hooks_token="secret")
        monkeypatch.setattr(
            media_upload, "ALLOWED_PATH_PREFIXES", (str(tmp_path) + "/",)
        )
        f = tmp_path / "naughty.exe"
        f.write_bytes(b"MZ")
        res = app_client.post(
            "/internal/openclaw/media/upload-local",
            headers={"Authorization": "Bearer secret"},
            json={"local_path": str(f)},
        )
        assert res.status_code == 415

    def test_uses_override_filename_for_extension_gate(
        self,
        app_client: TestClient,
        data_dir: Path,
        allowed_file: Path,
        stub_bearer: None,
        stub_cloud: MagicMock,
    ) -> None:
        _seed_manifest(data_dir, hooks_token="secret")
        res = app_client.post(
            "/internal/openclaw/media/upload-local",
            headers={"Authorization": "Bearer secret"},
            json={"local_path": str(allowed_file), "filename": "renamed.jpg"},
        )
        assert res.status_code == 200
        kwargs = stub_cloud.call_args.kwargs
        assert kwargs["filename"] == "renamed.jpg"
        assert kwargs["content_type"] == "image/jpeg"
