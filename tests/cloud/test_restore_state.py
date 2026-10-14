from __future__ import annotations

import io
import json
import stat
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import Client as HttpxClient
from sellerclaw_agent.cloud.restore_state import run_restore_if_needed
from sellerclaw_agent.cloud.state_backup import build_state_backup_archive
from sellerclaw_agent.cloud.whatsapp_session_backup import build_whatsapp_session_archive

pytestmark = pytest.mark.unit

_BROWSER_PATH = "/agent/connection/state-backup"
_WHATSAPP_PATH = "/agent/connection/whatsapp/session"

Routes = dict[str, httpx.Response]


@pytest.fixture
def cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[Routes], list[str]]:
    """Point the restore at a fake cloud; returns the list of requested paths."""
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_API_KEY", "secret-token")
    monkeypatch.setenv("SELLERCLAW_API_URL", "http://example.com")

    def install(routes: Routes) -> list[str]:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("authorization") == "Bearer secret-token"
            requested.append(request.url.path)
            return routes.get(request.url.path, httpx.Response(404))

        def _client_factory(**kwargs: Any) -> httpx.Client:
            kwargs.pop("transport", None)
            return HttpxClient(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr("sellerclaw_agent.cloud.restore_state.httpx.Client", _client_factory)
        return requested

    return install


def _browser_archive(tmp_path: Path) -> bytes:
    src = tmp_path / "upstream-browser"
    (src / "browser" / "openclaw" / "user-data" / "Default").mkdir(parents=True)
    (src / "browser" / "openclaw" / "user-data" / "Default" / "Cookies").write_text("jar", encoding="utf-8")
    archive = build_state_backup_archive(src)
    assert archive is not None
    return archive


def _whatsapp_archive(tmp_path: Path) -> bytes:
    src = tmp_path / "upstream-whatsapp"
    account = src / "credentials" / "whatsapp" / "default"
    account.mkdir(parents=True)
    (account / "creds.json").write_text(json.dumps({"me": {"id": "15550001111:1@s.whatsapp.net"}}))
    (account / "session-15550002222.0.json").write_text("{}")
    archive = build_whatsapp_session_archive(src)
    assert archive is not None
    return archive


def _browser_cookies(tmp_path: Path) -> Path:
    return tmp_path / "browser" / "openclaw" / "user-data" / "Default" / "Cookies"


def _whatsapp_creds(tmp_path: Path) -> Path:
    return tmp_path / "credentials" / "whatsapp" / "default" / "creds.json"


def test_run_restore_skips_without_agent_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    client_ctor = MagicMock()
    monkeypatch.setattr("sellerclaw_agent.cloud.restore_state.httpx.Client", client_ctor)
    run_restore_if_needed()
    client_ctor.assert_not_called()


def test_run_restore_restores_both_categories_on_an_empty_machine(
    tmp_path: Path,
    cloud: Callable[[Routes], list[str]],
) -> None:
    requested = cloud(
        {
            _BROWSER_PATH: httpx.Response(200, content=_browser_archive(tmp_path)),
            _WHATSAPP_PATH: httpx.Response(200, content=_whatsapp_archive(tmp_path)),
        }
    )
    run_restore_if_needed()
    assert requested == [_WHATSAPP_PATH, _BROWSER_PATH]
    assert _browser_cookies(tmp_path).read_text() == "jar"
    creds = _whatsapp_creds(tmp_path)
    assert json.loads(creds.read_text()) == {"me": {"id": "15550001111:1@s.whatsapp.net"}}
    # Credentials come back private, like Baileys writes them.
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    assert stat.S_IMODE(creds.parent.stat().st_mode) == 0o700
    assert (creds.parent / "session-15550002222.0.json").read_text() == "{}"


def test_run_restore_keeps_local_browser_logins_but_still_restores_whatsapp(
    tmp_path: Path,
    cloud: Callable[[Routes], list[str]],
) -> None:
    """A profile on disk is newer than any backup and wins — that says nothing about WhatsApp."""
    cookies = _browser_cookies(tmp_path)
    cookies.parent.mkdir(parents=True)
    cookies.write_text("local", encoding="utf-8")
    requested = cloud({_WHATSAPP_PATH: httpx.Response(200, content=_whatsapp_archive(tmp_path))})
    run_restore_if_needed()
    assert requested == [_WHATSAPP_PATH]
    assert cookies.read_text() == "local"
    assert _whatsapp_creds(tmp_path).is_file()


def test_run_restore_keeps_a_local_whatsapp_session(
    tmp_path: Path,
    cloud: Callable[[Routes], list[str]],
) -> None:
    creds = _whatsapp_creds(tmp_path)
    creds.parent.mkdir(parents=True)
    creds.write_text('{"me": "local"}', encoding="utf-8")
    requested = cloud({_BROWSER_PATH: httpx.Response(200, content=_browser_archive(tmp_path))})
    run_restore_if_needed()
    assert requested == [_BROWSER_PATH]
    assert creds.read_text() == '{"me": "local"}'
    assert _browser_cookies(tmp_path).read_text() == "jar"


def test_run_restore_after_clean_start_restores_only_whatsapp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloud: Callable[[Routes], list[str]],
) -> None:
    """clean=True wipes sessions and memory; the WhatsApp link is identity, not memory."""
    monkeypatch.setenv("RESET_STATE", "1")
    requested = cloud(
        {
            _BROWSER_PATH: httpx.Response(200, content=_browser_archive(tmp_path)),
            _WHATSAPP_PATH: httpx.Response(200, content=_whatsapp_archive(tmp_path)),
        }
    )
    run_restore_if_needed()
    assert requested == [_WHATSAPP_PATH]
    assert _whatsapp_creds(tmp_path).is_file()
    assert not _browser_cookies(tmp_path).exists()


def test_run_restore_downloads_browser_backup_when_only_chats_exist(
    tmp_path: Path,
    cloud: Callable[[Routes], list[str]],
) -> None:
    """Sessions on disk say nothing about browser logins — the restore must still run."""
    store = tmp_path / "agents" / "a" / "agent" / "openclaw-agent.sqlite"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"SQLite format 3\x00")
    cloud({_BROWSER_PATH: httpx.Response(200, content=_browser_archive(tmp_path))})
    run_restore_if_needed()
    assert _browser_cookies(tmp_path).read_text() == "jar"


def test_run_restore_noop_on_404(tmp_path: Path, cloud: Callable[[Routes], list[str]]) -> None:
    requested = cloud({})
    run_restore_if_needed()
    assert requested == [_WHATSAPP_PATH, _BROWSER_PATH]
    assert not (tmp_path / "browser").exists()
    assert not (tmp_path / "chrome-profile").exists()
    assert not (tmp_path / "credentials").exists()


@pytest.mark.parametrize(
    "whatsapp_response",
    [
        pytest.param(httpx.Response(500), id="cloud-error"),
        pytest.param(httpx.Response(200, content=b"not a tarball"), id="garbage"),
    ],
)
def test_run_restore_browser_survives_a_failed_whatsapp_restore(
    tmp_path: Path,
    cloud: Callable[[Routes], list[str]],
    whatsapp_response: httpx.Response,
) -> None:
    cloud(
        {
            _BROWSER_PATH: httpx.Response(200, content=_browser_archive(tmp_path)),
            _WHATSAPP_PATH: whatsapp_response,
        }
    )
    run_restore_if_needed()
    assert _browser_cookies(tmp_path).read_text() == "jar"
    assert not _whatsapp_creds(tmp_path).exists()


def test_run_restore_refuses_a_whatsapp_archive_escaping_its_directory(
    tmp_path: Path,
    cloud: Callable[[Routes], list[str]],
) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        raw = b"{}"
        info = tarfile.TarInfo(name="openclaw.json")
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
    cloud({_WHATSAPP_PATH: httpx.Response(200, content=buffer.getvalue())})
    run_restore_if_needed()
    assert not (tmp_path / "openclaw.json").exists()


def test_run_restore_does_not_plant_a_legacy_session_store(
    tmp_path: Path,
    cloud: Callable[[Routes], list[str]],
) -> None:
    """A cold start against a pre-2026-08-20 archive must not resurrect OpenClaw sessions.

    Such an archive holds nothing but sessions, and unpacking them made the gateway refuse to
    start ("Legacy session store requires migration") on every boot of a freshly built machine.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, raw in (
            ("agents/supervisor/sessions/sessions.json", b"{}"),
            ("agents/supervisor/sessions/6aaf.jsonl", b'{"role":"user"}\n'),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
    cloud({_BROWSER_PATH: httpx.Response(200, content=buffer.getvalue())})
    run_restore_if_needed()

    assert not (tmp_path / "agents").exists()
