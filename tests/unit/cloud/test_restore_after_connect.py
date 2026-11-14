"""Browser sign-ins and WhatsApp restored on the first connect after a sign-in (the move to a computer)."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from sellerclaw_agent.cloud.restore_after_connect import (
    MARKER_FILE_NAME,
    PostConnectRestore,
    token_fingerprint,
)

pytestmark = pytest.mark.unit

_TOKEN = "sca_first_sign_in"


def _with_browser_sign_ins(state_dir: Path) -> None:
    cookies = state_dir / "browser" / "openclaw" / "user-data" / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True, exist_ok=True)
    cookies.write_bytes(b"jar")


def _with_whatsapp_session(state_dir: Path) -> None:
    creds = state_dir / "credentials" / "whatsapp" / "default" / "creds.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text("{}")


class _Restore:
    """The restore itself: counts runs; ``gate`` holds it until set, ``error`` makes it fail."""

    def __init__(self, *, error: Exception | None = None, gate: threading.Event | None = None) -> None:
        self.runs = 0
        self.error = error
        self.gate = gate

    def __call__(self) -> None:
        self.runs += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error


@pytest.fixture()
def make_post_connect_restore(tmp_path: Path) -> Callable[..., tuple[PostConnectRestore, _Restore, Path, Path]]:
    """``(restore, the restore function, data dir, state dir)`` sharing one data dir across calls."""
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    data_dir.mkdir()
    state_dir.mkdir()

    def _factory(**kwargs: object) -> tuple[PostConnectRestore, _Restore, Path, Path]:
        fn = _Restore(**kwargs)  # type: ignore[arg-type]
        return PostConnectRestore(data_dir=data_dir, state_dir=state_dir, restore=fn), fn, data_dir, state_dir

    return _factory


async def test_the_first_connect_restores_and_remembers_it(make_post_connect_restore) -> None:
    restore, fn, data_dir, _state = make_post_connect_restore()

    assert restore.maybe_start(token=_TOKEN, openclaw_status="stopped") is True
    assert await restore.wait(timeout_s=2.0) is True

    assert fn.runs == 1
    marker = json.loads((data_dir / MARKER_FILE_NAME).read_text())
    assert marker["token_fingerprint"] == token_fingerprint(_TOKEN)
    assert _TOKEN not in (data_dir / MARKER_FILE_NAME).read_text(), "the token itself is never written"


async def test_once_per_sign_in_not_on_every_reconnect(make_post_connect_restore) -> None:
    first, _fn, _data, _state = make_post_connect_restore()
    first.maybe_start(token=_TOKEN, openclaw_status="stopped")
    await first.wait(timeout_s=2.0)
    # The agent server restarted, same token: the restore already happened for it.
    again, fn_again, _data, _state = make_post_connect_restore()

    assert again.maybe_start(token=_TOKEN, openclaw_status="stopped") is False
    assert fn_again.runs == 0


async def test_a_new_sign_in_restores_again(make_post_connect_restore) -> None:
    first, _fn, _data, _state = make_post_connect_restore()
    first.maybe_start(token=_TOKEN, openclaw_status="stopped")
    await first.wait(timeout_s=2.0)
    after_new_sign_in, fn, _data, _state = make_post_connect_restore()

    assert after_new_sign_in.maybe_start(token="sca_second_sign_in", openclaw_status="stopped") is True
    await after_new_sign_in.wait(timeout_s=2.0)
    assert fn.runs == 1


async def test_one_restore_per_process(make_post_connect_restore) -> None:
    restore, fn, _data, _state = make_post_connect_restore()
    restore.maybe_start(token=_TOKEN, openclaw_status="stopped")

    assert restore.maybe_start(token="sca_other", openclaw_status="stopped") is False
    await restore.wait(timeout_s=2.0)
    assert fn.runs == 1


@pytest.mark.parametrize(
    ("token", "openclaw_status"),
    [
        pytest.param(None, "stopped", id="no-token"),
        pytest.param("", "stopped", id="empty-token"),
        pytest.param(_TOKEN, "running", id="openclaw-running"),
        pytest.param(_TOKEN, "starting", id="openclaw-coming-up"),
    ],
)
async def test_nothing_is_written_under_a_running_agent_or_without_a_sign_in(
    make_post_connect_restore, token: str | None, openclaw_status: str
) -> None:
    restore, fn, data_dir, _state = make_post_connect_restore()

    assert restore.maybe_start(token=token, openclaw_status=openclaw_status) is False

    assert fn.runs == 0
    assert not (data_dir / MARKER_FILE_NAME).exists(), "tried again on a later connect"


@pytest.mark.parametrize(
    ("browser", "whatsapp", "starts"),
    [
        pytest.param(False, False, True, id="nothing-local"),
        pytest.param(True, False, True, id="whatsapp-missing"),
        pytest.param(False, True, True, id="browser-missing"),
        pytest.param(True, True, False, id="both-local-are-newer"),
    ],
)
async def test_restores_only_what_this_computer_lacks(
    make_post_connect_restore, browser: bool, whatsapp: bool, starts: bool
) -> None:
    restore, fn, data_dir, state_dir = make_post_connect_restore()
    if browser:
        _with_browser_sign_ins(state_dir)
    if whatsapp:
        _with_whatsapp_session(state_dir)

    restore.maybe_start(token=_TOKEN, openclaw_status="stopped")
    assert await restore.wait(timeout_s=2.0) is True

    assert fn.runs == (1 if starts else 0)
    assert (data_dir / MARKER_FILE_NAME).exists(), "checked once for this sign-in either way"


async def test_a_failed_restore_is_tried_again_next_time(make_post_connect_restore) -> None:
    restore, fn, data_dir, _state = make_post_connect_restore(error=OSError("cloud unreachable"))

    restore.maybe_start(token=_TOKEN, openclaw_status="stopped")
    assert await restore.wait(timeout_s=2.0) is True

    assert fn.runs == 1
    assert not (data_dir / MARKER_FILE_NAME).exists()


async def test_start_waits_only_so_long(make_post_connect_restore) -> None:
    """The cloud expects a command answer within a minute: START goes ahead without the restore."""
    gate = threading.Event()
    restore, fn, data_dir, _state = make_post_connect_restore(gate=gate)
    restore.maybe_start(token=_TOKEN, openclaw_status="stopped")

    assert await restore.wait(timeout_s=0.05) is False

    gate.set()
    assert await restore.wait(timeout_s=2.0) is True, "the restore itself was not cancelled"
    assert fn.runs == 1
    assert (data_dir / MARKER_FILE_NAME).exists()


async def test_wait_without_a_restore_returns_at_once(make_post_connect_restore) -> None:
    restore, _fn, _data, _state = make_post_connect_restore()

    assert await asyncio.wait_for(restore.wait(timeout_s=30.0), timeout=1.0) is True
