"""The archive is vetted in the cloud from its table of contents — which a hostile archive can
forge. These tests cover the other half: the rules enforced while the data is actually unpacked,
on the machine that has to live with the result."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import stat
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_COMMAND = Path(__file__).resolve().parents[3] / "runtime" / "commands" / "sellerclaw_unzip"


def _load_command() -> ModuleType:
    """The command ships as an extensionless script on PATH — import it by path."""
    spec = importlib.util.spec_from_loader(
        "sellerclaw_unzip",
        importlib.machinery.SourceFileLoader("sellerclaw_unzip", str(_COMMAND)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


unzip = _load_command()


def _write_zip(path: Path, members: dict[str, bytes], *, compress: bool = True) -> Path:
    mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", compression=mode) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def test_a_normal_archive_is_unpacked(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "evidence.zip",
        {"report.csv": b"a,b\n1,2\n", "shots/one.png": b"\x89PNG\r\n\x1a\nfake"},
    )
    destination = tmp_path / "out"

    extracted = unzip.extract(archive, destination)

    assert sorted(path.relative_to(destination).as_posix() for path in extracted) == [
        "report.csv",
        "shots/one.png",
    ]
    assert (destination / "report.csv").read_bytes() == b"a,b\n1,2\n"


def test_a_symlink_escape_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    """`unzip` follows the link and writes through it — this is the escape that needs no `..`."""
    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        link = zipfile.ZipInfo("logs")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, str(tmp_path / "secrets"))
        archive.writestr("logs/config.json", b'{"pwned": true}')
    destination = tmp_path / "out"

    with pytest.raises(unzip.RejectedArchive, match="symlink or special file"):
        unzip.extract(archive_path, destination)

    assert list(destination.rglob("*")) == []


def test_a_path_escape_is_refused(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "evil.zip", {"../escaped.txt": b"x"})

    with pytest.raises(unzip.RejectedArchive, match="unsafe path"):
        unzip.extract(archive, tmp_path / "out")


def test_a_forged_size_header_is_caught_by_the_checksum(tmp_path: Path) -> None:
    """An archive that lies about a member's size is refused, and nothing is written.

    This is the case the cloud's table-of-contents check cannot see: the declared size says one
    thing, the data says another. The archive's own CRC gives it away as soon as the bytes are
    read for real.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("boom.txt", b"0" * 100_000)
    raw = bytearray(buffer.getvalue())
    # Rewrite the *declared* uncompressed size in the central directory to a harmless 10 bytes.
    central = raw.rfind(b"PK\x01\x02")
    raw[central + 24 : central + 28] = (10).to_bytes(4, "little")
    archive_path = tmp_path / "liar.zip"
    archive_path.write_bytes(bytes(raw))

    with pytest.raises(unzip.RejectedArchive, match="corrupted or forged"):
        unzip.extract(archive_path, tmp_path / "out")

    assert list((tmp_path / "out").rglob("*")) == []


def test_a_bomb_is_stopped_mid_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An honest header on a member that really does explode: the cap bites while decompressing."""
    monkeypatch.setattr(unzip, "MAX_MEMBER_BYTES", 1024)
    archive = _write_zip(tmp_path / "boom.zip", {"boom.txt": b"0" * 100_000})

    with pytest.raises(unzip.RejectedArchive, match="decompression bomb"):
        unzip.extract(archive, tmp_path / "out")

    assert list((tmp_path / "out").rglob("*")) == []


def test_too_many_members_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(unzip, "MAX_MEMBERS", 3)
    archive = _write_zip(
        tmp_path / "many.zip",
        {f"file-{index}.txt": b"x" for index in range(4)},
        compress=False,
    )

    with pytest.raises(unzip.RejectedArchive, match="at most"):
        unzip.extract(archive, tmp_path / "out")


def test_an_encrypted_member_is_refused(tmp_path: Path) -> None:
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("report.csv", b"a,b\n")
    raw = bytearray(plain.getvalue())
    raw[6] |= 0x1
    central = raw.rfind(b"PK\x01\x02")
    raw[central + 8] |= 0x1
    archive_path = tmp_path / "locked.zip"
    archive_path.write_bytes(bytes(raw))

    with pytest.raises(unzip.RejectedArchive, match="password-protected"):
        unzip.extract(archive_path, tmp_path / "out")


def test_a_corrupt_archive_is_refused(tmp_path: Path) -> None:
    archive_path = tmp_path / "broken.zip"
    archive_path.write_bytes(b"PK\x03\x04 not really a zip")

    with pytest.raises(unzip.RejectedArchive, match="not a valid .zip"):
        unzip.extract(archive_path, tmp_path / "out")
