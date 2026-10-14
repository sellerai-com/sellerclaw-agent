from __future__ import annotations

import asyncio
import io
import os
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest
from sellerclaw_agent.cloud.whatsapp_session_backup import (
    SessionSnapshotUnstableError,
    WhatsappSessionBackupLoop,
    build_whatsapp_session_archive,
    has_local_whatsapp_session,
    remove_whatsapp_session,
    restore_whatsapp_session,
    whatsapp_disconnected_in,
)

from tests.cloud.conftest import FakeWhatsappSessionCloud

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _names(archive: bytes) -> set[str]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        return {member.name for member in tar.getmembers()}


def test_archive_holds_only_the_session_directory(
    tmp_path: Path,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    make_whatsapp_session_dir(tmp_path, extra={"creds.json.bak": '{"me": "old"}'})
    (tmp_path / "openclaw.json").write_text("{}")
    (tmp_path / "credentials" / "telegram.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "credentials" / "telegram.json").write_text("{}")

    archive = build_whatsapp_session_archive(tmp_path)

    assert archive is not None
    assert _names(archive) == {
        "credentials/whatsapp/default/creds.json",
        "credentials/whatsapp/default/creds.json.bak",
        "credentials/whatsapp/default/pre-key-1.json",
    }


def test_archive_is_none_without_a_linked_account(tmp_path: Path) -> None:
    (tmp_path / "credentials" / "whatsapp" / "default").mkdir(parents=True)
    (tmp_path / "credentials" / "whatsapp" / "default" / "pre-key-1.json").write_text("{}")
    assert has_local_whatsapp_session(tmp_path) is False
    assert build_whatsapp_session_archive(tmp_path) is None


def test_archive_refuses_a_file_caught_mid_write(
    tmp_path: Path,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    make_whatsapp_session_dir(tmp_path, extra={"session-15550002222.0.json": '{"chains": '})
    with pytest.raises(SessionSnapshotUnstableError, match="session-15550002222.0.json does not parse"):
        build_whatsapp_session_archive(tmp_path)


def test_remove_session(tmp_path: Path, make_whatsapp_session_dir: Callable[..., Path]) -> None:
    root = make_whatsapp_session_dir(tmp_path)
    os.chmod(root / "creds.json", 0o600)
    assert remove_whatsapp_session(tmp_path) is True
    assert not (tmp_path / "credentials" / "whatsapp").exists()
    assert remove_whatsapp_session(tmp_path) is False


def _loop(
    tmp_path: Path,
    cloud: FakeWhatsappSessionCloud,
    clock: _Clock,
    *,
    disconnected: Callable[[], bool] = lambda: False,
) -> WhatsappSessionBackupLoop:
    return WhatsappSessionBackupLoop(
        state_dir=tmp_path,
        cloud=cloud,
        owner_disconnected=disconnected,
        quiet_seconds=60,
        min_interval_seconds=300,
        max_staleness_seconds=900,
        clock=clock,
    )


async def test_loop_uploads_only_after_the_files_settle(
    tmp_path: Path,
    fake_whatsapp_session_cloud: FakeWhatsappSessionCloud,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    clock = _Clock()
    loop = _loop(tmp_path, fake_whatsapp_session_cloud, clock)
    assert await loop.tick() is False  # nothing linked

    make_whatsapp_session_dir(tmp_path)
    assert await loop.tick() is False  # just changed
    clock.now += 30
    assert await loop.tick() is False  # not quiet yet
    clock.now += 31
    assert await loop.tick() is True
    assert len(fake_whatsapp_session_cloud.uploads) == 1
    assert "credentials/whatsapp/default/creds.json" in _names(fake_whatsapp_session_cloud.uploads[0])

    clock.now += 1_000
    assert await loop.tick() is False  # unchanged since the upload
    assert len(fake_whatsapp_session_cloud.uploads) == 1


async def test_loop_respects_the_interval_unless_a_link_asks_for_a_copy(
    tmp_path: Path,
    fake_whatsapp_session_cloud: FakeWhatsappSessionCloud,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    clock = _Clock()
    loop = _loop(tmp_path, fake_whatsapp_session_cloud, clock)
    root = make_whatsapp_session_dir(tmp_path)
    await loop.tick()
    clock.now += 61
    assert await loop.tick() is True

    (root / "pre-key-2.json").write_text("{}")
    await loop.tick()
    clock.now += 61
    assert await loop.tick() is False  # quiet, but within 5 min of the last copy

    loop.request_upload_soon()
    assert await loop.tick() is True
    assert len(fake_whatsapp_session_cloud.uploads) == 2


async def test_loop_uploads_a_chatty_session_once_it_gets_too_stale(
    tmp_path: Path,
    fake_whatsapp_session_cloud: FakeWhatsappSessionCloud,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    """Signal sessions ratchet on every message; a busy account is never quiet for a minute."""
    clock = _Clock()
    loop = _loop(tmp_path, fake_whatsapp_session_cloud, clock)
    root = make_whatsapp_session_dir(tmp_path)
    session = root / "session-1.json"
    uploaded = False
    for step in range(40):
        session.write_text(f'{{"n": {step}}}')
        # Writes this fast can share one filesystem timestamp tick; move mtime on explicitly.
        os.utime(session, ns=(1_000_000_000 * (step + 1), 1_000_000_000 * (step + 1)))
        uploaded = await loop.tick() or uploaded
        if uploaded:
            break
        clock.now += 30
    assert uploaded
    assert clock.now - 1_000.0 >= 900


async def test_loop_retries_after_a_failed_upload_and_forgets_after_logout(
    tmp_path: Path,
    fake_whatsapp_session_cloud: FakeWhatsappSessionCloud,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    clock = _Clock()
    loop = _loop(tmp_path, fake_whatsapp_session_cloud, clock)
    make_whatsapp_session_dir(tmp_path)
    await loop.tick()
    clock.now += 61
    fake_whatsapp_session_cloud.fail_next = True
    assert await loop.tick() is False
    assert await loop.tick() is False  # pauses after a failure instead of retrying every tick
    clock.now += 60
    assert await loop.tick() is True
    assert len(fake_whatsapp_session_cloud.uploads) == 1

    loop.forget_uploaded()  # logout deleted the cloud copy; the same files must go up again
    remove_whatsapp_session(tmp_path)
    assert await loop.tick() is False
    make_whatsapp_session_dir(tmp_path)
    await loop.tick()
    clock.now += 61
    assert await loop.tick() is True
    assert len(fake_whatsapp_session_cloud.uploads) == 2


@pytest.mark.parametrize(
    ("manifest", "disconnected"),
    [
        pytest.param({"channels": {"whatsapp": {"enabled": False, "allowed_numbers": []}}}, True, id="disconnected"),
        pytest.param(
            {"channels": {"whatsapp": {"enabled": False, "allowed_numbers": ["+15550002222"]}}},
            False,
            id="switched-off-keeps-the-link",
        ),
        pytest.param(
            {"channels": {"whatsapp": {"enabled": True, "allowed_numbers": ["+15550002222"]}}}, False, id="on"
        ),
        pytest.param({"channels": {}}, False, id="no-whatsapp-block"),
        pytest.param(None, False, id="no-manifest-yet"),
    ],
)
def test_whatsapp_disconnected_in(manifest: dict[str, object] | None, disconnected: bool) -> None:
    assert whatsapp_disconnected_in(manifest) is disconnected


async def test_loop_deletes_the_session_once_the_owner_disconnected(
    tmp_path: Path,
    fake_whatsapp_session_cloud: FakeWhatsappSessionCloud,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    """Disconnect holds even when the logout command never reached this machine."""
    clock = _Clock()
    disconnected = False
    loop = _loop(tmp_path, fake_whatsapp_session_cloud, clock, disconnected=lambda: disconnected)
    make_whatsapp_session_dir(tmp_path)
    await loop.tick()
    clock.now += 61
    assert await loop.tick() is True

    disconnected = True
    assert await loop.tick() is False
    assert not (tmp_path / "credentials" / "whatsapp").exists()
    assert await loop.upload_now() is False
    assert len(fake_whatsapp_session_cloud.uploads) == 1


async def test_upload_now_skips_the_wait_but_not_a_repeat(
    tmp_path: Path,
    fake_whatsapp_session_cloud: FakeWhatsappSessionCloud,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    loop = _loop(tmp_path, fake_whatsapp_session_cloud, _Clock())
    make_whatsapp_session_dir(tmp_path)

    assert await loop.upload_now() is True  # just changed, yet copied: OpenClaw is stopped
    assert await loop.upload_now() is False  # the same files are already in the cloud
    assert len(fake_whatsapp_session_cloud.uploads) == 1


async def test_paused_holds_an_upload_until_the_logout_is_done(
    tmp_path: Path,
    fake_whatsapp_session_cloud: FakeWhatsappSessionCloud,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    loop = _loop(tmp_path, fake_whatsapp_session_cloud, _Clock())
    make_whatsapp_session_dir(tmp_path)

    async with loop.paused():
        upload = asyncio.ensure_future(loop.upload_now())
        await asyncio.sleep(0.01)
        assert not upload.done()
        remove_whatsapp_session(tmp_path)
        loop.forget_uploaded()

    assert await upload is False  # nothing left to copy
    assert fake_whatsapp_session_cloud.uploads == []


def test_restore_puts_the_whole_session_in_place(
    tmp_path: Path,
    make_whatsapp_session_dir: Callable[..., Path],
) -> None:
    source = tmp_path / "source"
    make_whatsapp_session_dir(source)
    archive = build_whatsapp_session_archive(source)
    assert archive is not None
    target = tmp_path / "target"
    (target / "credentials" / "whatsapp" / "default").mkdir(parents=True)
    (target / "credentials" / "whatsapp" / "default" / "pre-key-9.json").write_text("{}")  # leftover

    assert restore_whatsapp_session(target, archive) == 2

    root = target / "credentials" / "whatsapp"
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == [
        "default/creds.json",
        "default/pre-key-1.json",
    ]
    assert (root / "default" / "creds.json").stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700
    assert [p.name for p in (target / "credentials").iterdir()] == ["whatsapp"]


def test_restore_writes_nothing_when_a_later_entry_is_unsafe(tmp_path: Path) -> None:
    """A creds.json without its keys would pass for a session and block every later restore."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in ("credentials/whatsapp/default/creds.json", "credentials/whatsapp/../../escape.json"):
            data = b"{}"
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    with pytest.raises(ValueError, match="Unsafe path"):
        restore_whatsapp_session(tmp_path, buffer.getvalue())

    assert not has_local_whatsapp_session(tmp_path)
    assert not (tmp_path / "escape.json").exists()
