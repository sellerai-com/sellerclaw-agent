"""OpenClaw's state databases, as small as the ChatGPT sign-in code needs them."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

#: The two tables OpenClaw 2026.11.2 keeps auth profiles in (schemas copied from a live install).
_SHARED_SCHEMA = (
    "CREATE TABLE config_machine_state (state_key TEXT NOT NULL PRIMARY KEY, value_json TEXT NOT NULL, "
    "updated_at_ms INTEGER NOT NULL) STRICT"
)
_AGENT_SCHEMA = (
    "CREATE TABLE auth_profile_store (store_key TEXT NOT NULL PRIMARY KEY, store_json TEXT NOT NULL, "
    "updated_at INTEGER NOT NULL) STRICT"
)


def chatgpt_profile(*, access: str = "acc-1", refresh: str = "ref-1", account: str = "acct") -> dict[str, Any]:
    """A ChatGPT OAuth profile in the shape OpenClaw stores (``{access, refresh, expires, accountId}``)."""
    return {
        "type": "oauth",
        "provider": "openai",
        "access": access,
        "refresh": refresh,
        "expires": 1794600000000,
        "accountId": account,
    }


#: The API-key profiles a credits install already keeps in the shared store; they must survive.
OUR_KEYS: dict[str, Any] = {
    "litellm:default": {"type": "api_key", "provider": "litellm", "key": "sk-litellm"},
    "google:default": {"type": "api_key", "provider": "google", "key": "g-key"},
}


def store(profiles: dict[str, Any], lifetimes: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"version": 1, "profiles": profiles}
    if lifetimes is not None:
        out["authorizationLifetimes"] = lifetimes
    return out


class OpenclawState:
    """``~/.openclaw`` with the shared store and the main agent's own store."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.shared_db = root / "state" / "openclaw.sqlite"
        self.agent_db = root / "agents" / "supervisor" / "agent" / "openclaw-agent.sqlite"

    def create(self, *, shared: dict[str, Any] | None, agent: dict[str, Any] | None) -> OpenclawState:
        self.create_shared(shared)
        self.agent_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.agent_db) as conn:
            conn.execute(_AGENT_SCHEMA)
            if agent is not None:
                conn.execute("INSERT INTO auth_profile_store VALUES ('primary', ?, 1)", (json.dumps(agent),))
        return self

    def create_shared(self, shared: dict[str, Any] | None) -> None:
        self.shared_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.shared_db) as conn:
            conn.execute(_SHARED_SCHEMA)
            if shared is not None:
                conn.execute(
                    "INSERT INTO config_machine_state VALUES ('authProfiles.store', ?, 1)", (json.dumps(shared),)
                )

    def shared(self) -> dict[str, Any] | None:
        with sqlite3.connect(self.shared_db) as conn:
            row = conn.execute(
                "SELECT value_json FROM config_machine_state WHERE state_key = 'authProfiles.store'"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def agent(self) -> dict[str, Any] | None:
        with sqlite3.connect(self.agent_db) as conn:
            row = conn.execute("SELECT store_json FROM auth_profile_store WHERE store_key = 'primary'").fetchone()
        return json.loads(row[0]) if row else None


@pytest.fixture()
def make_openclaw_state(tmp_path: Path) -> Callable[..., OpenclawState]:
    """``name`` gives another state next to the first — a container an update replaced, say.
    ``shared=None, agent=None, empty=True`` leaves the directory without any database yet."""

    def _make(
        *,
        shared: dict[str, Any] | None = None,
        agent: dict[str, Any] | None = None,
        name: str = "openclaw",
        empty: bool = False,
    ) -> OpenclawState:
        state = OpenclawState(tmp_path / name)
        return state if empty else state.create(shared=shared, agent=agent)

    return _make


@pytest.fixture()
def make_chatgpt_profile() -> Callable[..., dict[str, Any]]:
    return chatgpt_profile


@pytest.fixture()
def make_auth_store() -> Callable[..., dict[str, Any]]:
    return store


@pytest.fixture()
def our_keys() -> dict[str, Any]:
    return dict(OUR_KEYS)


@pytest.fixture(autouse=True)
def no_gateway_processes(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """``settle`` waits for a gateway process to exit; this machine's own ``/proc`` (which lists
    a local agent container's gateway too) must not decide a test."""
    from sellerclaw_agent.subscription import chatgpt_auth

    monkeypatch.setattr(chatgpt_auth, "PROC_DIR", tmp_path_factory.mktemp("proc"))
