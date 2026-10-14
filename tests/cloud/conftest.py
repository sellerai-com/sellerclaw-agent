from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from openclaw_diagnostics.gateway import GatewayUnreachableError


@pytest.fixture(autouse=True)
def _isolate_home_for_cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``$HOME`` to ``tmp_path`` for every cloud test.

    ``CredentialsStorage.save()`` / ``.clear()`` now mirror the token into the
    sellerclaw-cli config at ``~/.config/sellerclaw/config.toml``. Without this
    fixture, unit tests that exercise those methods would write into the
    developer's real home directory.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    yield tmp_path


# --- WhatsApp channel fakes ---------------------------------------------------------------


class FakeWhatsappGateway:
    """Scripted gateway: each method answers from its own queue; the last answer repeats.

    An answer that is an exception is raised instead of returned. ``unreachable`` makes every
    connect fail the way a stopped OpenClaw does.
    """

    def __init__(self) -> None:
        self.answers: dict[str, list[Any]] = {}
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []
        self.unreachable = False

    def script(self, method: str, *answers: Any) -> None:
        self.answers.setdefault(method, []).extend(answers)

    def factory(self) -> _FakeWhatsappConnection:
        return _FakeWhatsappConnection(self)

    def methods_called(self) -> list[str]:
        return [method for method, _params, _timeout in self.calls]


class _FakeWhatsappConnection:
    def __init__(self, gateway: FakeWhatsappGateway) -> None:
        self._gateway = gateway

    async def __aenter__(self) -> _FakeWhatsappConnection:
        if self._gateway.unreachable:
            raise GatewayUnreachableError("gateway unreachable at ws://127.0.0.1:7789")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        self._gateway.calls.append((method, dict(params or {}), timeout))
        queue = self._gateway.answers.get(method)
        if not queue:
            raise AssertionError(f"unexpected gateway call: {method}")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeWhatsappLinkCloud:
    """Records every delivered link state; ``accept=False`` plays the cloud's 404.

    ``fail_statuses`` makes the first N posts of a status fail like a 5xx (recorded in ``failed``).
    """

    def __init__(self, *, accept: bool = True, fail_statuses: dict[str, int] | None = None) -> None:
        self.accept = accept
        self.states: list[dict[str, Any]] = []
        self.failed: list[str] = []
        self._fail_left = dict(fail_statuses or {})

    async def post_whatsapp_link_state(self, *, login_id: UUID, state: dict[str, Any]) -> bool:
        _ = login_id
        status = str(state["status"])
        if self._fail_left.get(status, 0) > 0:
            self._fail_left[status] -= 1
            self.failed.append(status)
            raise RuntimeError("cloud answered 503")
        self.states.append(state)
        return self.accept

    def statuses(self) -> list[str]:
        return [str(state["status"]) for state in self.states]


class FakeWhatsappSessionCloud:
    def __init__(self) -> None:
        self.uploads: list[bytes] = []
        self.fail_next = False

    async def upload_whatsapp_session(self, archive: bytes) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("cloud unavailable")
        self.uploads.append(archive)


@pytest.fixture
def fake_whatsapp_gateway() -> FakeWhatsappGateway:
    return FakeWhatsappGateway()


@pytest.fixture
def fake_whatsapp_link_cloud() -> FakeWhatsappLinkCloud:
    return FakeWhatsappLinkCloud()


@pytest.fixture
def fake_whatsapp_session_cloud() -> FakeWhatsappSessionCloud:
    return FakeWhatsappSessionCloud()


@pytest.fixture
def make_whatsapp_status_payload() -> Callable[..., dict[str, Any]]:
    """A ``channels.status {channel: "whatsapp"}`` payload as the gateway returns it."""

    def make(
        *,
        linked: bool | None = True,
        e164: str | None = "+15550001111",
        connected: bool = True,
        health_state: str | None = "healthy",
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "configured": True,
            "connected": connected,
            "self": {"e164": e164 if linked else None, "jid": None, "lid": None},
            "lastError": None,
        }
        if linked is not None:
            summary["linked"] = linked
        if health_state is not None:
            summary["healthState"] = health_state
        return {"channels": {"whatsapp": summary}, "channelAccounts": {"whatsapp": []}}

    return make


@pytest.fixture
def make_whatsapp_session_dir() -> Callable[..., Path]:
    """Write a WhatsApp auth dir (as Baileys leaves it) under ``state_dir``."""

    def make(state_dir: Path, *, account: str = "default", extra: dict[str, str] | None = None) -> Path:
        root = state_dir / "credentials" / "whatsapp" / account
        root.mkdir(parents=True, exist_ok=True)
        (root / "creds.json").write_text(json.dumps({"me": {"id": "15550001111:1@s.whatsapp.net"}}))
        (root / "pre-key-1.json").write_text("{}")
        for name, content in (extra or {}).items():
            (root / name).write_text(content)
        return root

    return make
