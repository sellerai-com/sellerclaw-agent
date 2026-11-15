"""The owner's ChatGPT sign-in for the agent: OpenClaw's own device-code login, kept across updates.

**Who signs in.** OpenClaw does: ``openclaw models auth login --provider openai --device-code`` shows
the owner a link and a code in their terminal, they confirm it on OpenAI's page, and OpenClaw stores
the resulting OAuth profile. Nothing here asks for, prints or sends the credential anywhere.

**Where it lives.** OpenClaw keeps profiles in its state databases. Agents read one shared store —
``state/openclaw.sqlite``, ``config_machine_state['authProfiles.store']`` — whenever they hold no
profile of their own, and a refresh writes back to whichever store owns the profile. So one sign-in
in the shared store serves the main agent and every specialist, and a rotated refresh token is
rewritten in one place. The device-code login is run for the main agent (OpenClaw requires naming
one) and lands in that agent's own store; :func:`settle`, run while the gateway is stopped, moves it
to the shared store.

**Which store is "shared".** The state database only once the fact ``auth.sharedStore`` says
``{"location": "state-db"}``; without it OpenClaw reads the implicit ``main`` agent's database, which
our installs never have. OpenClaw records the fact on the first shared write of a fresh install — a
sign-in for one agent is not one — so :func:`settle` records it under the same condition before it
relies on the state database (:func:`_state_db_shared`).

**Across ``sellerclaw-agent update``.** OpenClaw's state lives inside the container and an update
replaces it; only the data volume survives. :func:`snapshot` keeps a copy of the ChatGPT profiles —
those alone — in ``<data dir>/ai-subscription/chatgpt-auth.json`` (owner-only file; the cloud state
backup leaves this folder out). :func:`settle` puts it back before the gateway starts. The monitor
refreshes the copy as OpenClaw refreshes the token, so what comes back is the current generation.

**While the owner signs in.** The ``openai`` provider plugin is off on credits, and an online login
only offers what the running gateway has loaded. :func:`mark_login_pending` lets the config switch
the plugin on — models untouched — until the sign-in finishes or is abandoned.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sellerclaw_agent.subscription.state import (
    atomic_write_json,
    load_choice,
    read_json,
    subscription_dir,
)

#: OpenClaw's provider id for ChatGPT / Codex OAuth.
OPENAI_PROVIDER: Final = "openai"
#: The agent the device-code login is run for (OpenClaw wants one named when there are several).
AUTH_AGENT_ID: Final = "supervisor"

SNAPSHOT_FILE_NAME: Final = "chatgpt-auth.json"
PENDING_FILE_NAME: Final = "chatgpt-login.json"
#: A sign-in nobody finished stops holding the provider plugin on after this.
PENDING_TTL: Final = timedelta(minutes=20)

_SHARED_STORE_KEY: Final = "authProfiles.store"
#: OpenClaw's fact naming where the shared auth store lives (see the module notes).
_SHARED_OWNERSHIP_KEY: Final = "auth.sharedStore"
_STATE_DB_LOCATION: Final = "state-db"
#: The implicit agent whose database is the shared store until the fact above relocates it.
_LEGACY_MAIN_AGENT_ID: Final = "main"
_AGENT_STORE_KEY: Final = "primary"
_SQLITE_TIMEOUT_S: Final = 15.0
#: An offline OpenClaw command that creates the state database on a fresh container (see
#: :func:`_ensure_shared_db`); it reads, it does not sign anyone in.
_INIT_STATE_COMMAND: Final = ("openclaw", "models", "auth", "list", "--provider", OPENAI_PROVIDER)
_INIT_STATE_TIMEOUT_S: Final = 180.0


def openclaw_state_dir() -> Path:
    return Path(os.environ.get("OPENCLAW_STATE_DIR") or "/home/node/.openclaw")


def _shared_db(state_dir: Path) -> Path:
    return state_dir / "state" / "openclaw.sqlite"


def _agent_db(state_dir: Path) -> Path:
    return state_dir / "agents" / AUTH_AGENT_ID / "agent" / "openclaw-agent.sqlite"


def _legacy_main_db(state_dir: Path) -> Path:
    return state_dir / "agents" / _LEGACY_MAIN_AGENT_ID / "agent" / "openclaw-agent.sqlite"


def snapshot_path(data_dir: Path) -> Path:
    return subscription_dir(data_dir) / SNAPSHOT_FILE_NAME


def _pending_path(data_dir: Path) -> Path:
    return subscription_dir(data_dir) / PENDING_FILE_NAME


# --- the ChatGPT profiles inside an OpenClaw store ---------------------------------------------


def _is_chatgpt(profile: object) -> bool:
    return isinstance(profile, dict) and profile.get("provider") == OPENAI_PROVIDER and profile.get("type") == "oauth"


def _usable(profile: dict[str, Any]) -> bool:
    """A profile that still carries its tokens — not the inert marker OpenClaw leaves mid-refresh."""
    return bool(profile.get("access") or profile.get("refresh"))


@dataclass(frozen=True)
class ChatgptProfiles:
    """The ChatGPT OAuth profiles of one store, with their authorization lifetimes."""

    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    lifetimes: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.profiles)

    def to_json(self) -> dict[str, Any]:
        return {"profiles": self.profiles, "authorizationLifetimes": self.lifetimes}


def chatgpt_profiles(store: dict[str, Any] | None) -> ChatgptProfiles:
    """The usable ChatGPT profiles of a store (an OpenClaw ``{version, profiles, …}`` document)."""
    if not store:
        return ChatgptProfiles()
    raw = store.get("profiles")
    profiles = {
        pid: dict(profile)
        for pid, profile in (raw.items() if isinstance(raw, dict) else ())
        if _is_chatgpt(profile) and _usable(profile)
    }
    lifetimes_raw = store.get("authorizationLifetimes")
    lifetimes = {
        pid: value for pid, value in (lifetimes_raw.items() if isinstance(lifetimes_raw, dict) else ()) if pid in profiles
    }
    return ChatgptProfiles(profiles=profiles, lifetimes=lifetimes)


def _without_chatgpt(store: dict[str, Any]) -> dict[str, Any]:
    """The store with every ChatGPT profile (usable or not) taken out."""
    raw = store.get("profiles")
    gone = {pid for pid, profile in (raw.items() if isinstance(raw, dict) else ()) if _is_chatgpt(profile)}
    out = dict(store)
    out["profiles"] = {pid: p for pid, p in (raw.items() if isinstance(raw, dict) else ()) if pid not in gone}
    lifetimes = store.get("authorizationLifetimes")
    if isinstance(lifetimes, dict):
        out["authorizationLifetimes"] = {pid: v for pid, v in lifetimes.items() if pid not in gone}
    return out


def _with_chatgpt(store: dict[str, Any] | None, add: ChatgptProfiles) -> dict[str, Any]:
    """The store with ``add`` in it, replacing any ChatGPT profile it had."""
    base = _without_chatgpt(store) if store else {"version": 1, "profiles": {}}
    base["profiles"] = {**base.get("profiles", {}), **add.profiles}
    if add.lifetimes:
        lifetimes = base.get("authorizationLifetimes")
        base["authorizationLifetimes"] = {**(lifetimes if isinstance(lifetimes, dict) else {}), **add.lifetimes}
    return base


# --- reading and writing OpenClaw's databases ---------------------------------------------------


@contextlib.contextmanager
def _connect(path: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    # A writer manages its own transaction (``BEGIN IMMEDIATE`` in :func:`_update_store`).
    uri = f"file:{path}" if write else f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=_SQLITE_TIMEOUT_S, isolation_level=None if write else "DEFERRED")
    try:
        yield conn
    finally:
        conn.close()


def _load(conn: sqlite3.Connection, sql: str, key: str) -> dict[str, Any] | None:
    try:
        row = conn.execute(sql, (key,)).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_SHARED_SELECT: Final = "SELECT value_json FROM config_machine_state WHERE state_key = ?"
_SHARED_UPSERT: Final = (
    "INSERT INTO config_machine_state (state_key, value_json, updated_at_ms) VALUES (?, ?, ?) "
    "ON CONFLICT(state_key) DO UPDATE SET value_json = excluded.value_json, updated_at_ms = excluded.updated_at_ms"
)
_AGENT_SELECT: Final = "SELECT store_json FROM auth_profile_store WHERE store_key = ?"
_AGENT_UPSERT: Final = (
    "INSERT INTO auth_profile_store (store_key, store_json, updated_at) VALUES (?, ?, ?) "
    "ON CONFLICT(store_key) DO UPDATE SET store_json = excluded.store_json, updated_at = excluded.updated_at"
)


def _read_store(path: Path, select: str, key: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with _connect(path, write=False) as conn:
            return _load(conn, select, key)
    except sqlite3.Error:
        return None


class StoreUnreadableError(Exception):
    """An auth store that exists but cannot be read as one: never written over — what it holds
    besides ChatGPT (our own provider keys) would be lost."""


def _update_store(
    path: Path,
    select: str,
    upsert: str,
    key: str,
    change: Callable[[dict[str, Any] | None], dict[str, Any] | None],
) -> bool:
    """Read the store, ``change`` it, write it back — in one ``BEGIN IMMEDIATE`` transaction, so
    nothing writes in between. ``change`` returns ``None`` to leave the store as it is.

    Raises :class:`sqlite3.Error` / :class:`StoreUnreadableError` instead of taking a store it
    could not read for an empty one. Returns whether it wrote.
    """
    with _connect(path, write=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(select, (key,)).fetchone()
            current: dict[str, Any] | None = None
            if row is not None:
                try:
                    current = json.loads(row[0])
                except (TypeError, ValueError) as exc:
                    raise StoreUnreadableError(f"{path.name}: {key} is not JSON") from exc
                if not isinstance(current, dict):
                    raise StoreUnreadableError(f"{path.name}: {key} is not an object")
            updated = change(current)
            if updated is not None:
                conn.execute(upsert, (key, json.dumps(updated, separators=(",", ":")), int(time.time() * 1000)))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return updated is not None


def _update_shared(state_dir: Path, change: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> bool:
    return _update_store(_shared_db(state_dir), _SHARED_SELECT, _SHARED_UPSERT, _SHARED_STORE_KEY, change)


def _update_agent(state_dir: Path, change: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> bool:
    return _update_store(_agent_db(state_dir), _AGENT_SELECT, _AGENT_UPSERT, _AGENT_STORE_KEY, change)


def _drop_chatgpt(store: dict[str, Any] | None) -> dict[str, Any] | None:
    """``store`` without its ChatGPT profiles, or ``None`` when it holds none (nothing to write)."""
    if store is None or not any(_is_chatgpt(p) for p in (store.get("profiles") or {}).values()):
        return None
    return _without_chatgpt(store)


def live_profiles(state_dir: Path) -> ChatgptProfiles:
    """The ChatGPT sign-in OpenClaw holds now: the main agent's own (a fresh login) first, else shared."""
    local = chatgpt_profiles(_read_store(_agent_db(state_dir), _AGENT_SELECT, _AGENT_STORE_KEY))
    if local:
        return local
    return chatgpt_profiles(_read_store(_shared_db(state_dir), _SHARED_SELECT, _SHARED_STORE_KEY))


def has_sign_in(state_dir: Path) -> bool:
    return bool(live_profiles(state_dir))


# --- the copy on the data volume ----------------------------------------------------------------


def load_snapshot(data_dir: Path) -> ChatgptProfiles:
    return chatgpt_profiles(read_json(snapshot_path(data_dir)))


def snapshot(data_dir: Path, state_dir: Path) -> bool:
    """Keep the current ChatGPT sign-in on the data volume; ``True`` when one is kept.

    Rewritten only when it changed — OpenClaw refreshing the token is exactly such a change, and the
    copy has to follow it: a restored older refresh token may already be spent.
    """
    live = live_profiles(state_dir)
    if not live:
        return snapshot_path(data_dir).is_file()
    if load_snapshot(data_dir) != live:
        atomic_write_json(snapshot_path(data_dir), {**live.to_json(), "saved_at": datetime.now(tz=UTC).isoformat()})
    return True


def forget(data_dir: Path) -> None:
    """Drop the copy (the live profiles go at the next :func:`settle`, with the gateway stopped)."""
    snapshot_path(data_dir).unlink(missing_ok=True)


# --- a sign-in in progress ----------------------------------------------------------------------


def mark_login_pending(data_dir: Path, *, now: datetime | None = None) -> None:
    atomic_write_json(_pending_path(data_dir), {"started_at": (now or datetime.now(tz=UTC)).isoformat()})


def clear_login_pending(data_dir: Path) -> None:
    _pending_path(data_dir).unlink(missing_ok=True)


def login_pending(data_dir: Path, *, now: datetime | None = None) -> bool:
    """A ChatGPT sign-in was started here and not abandoned (it expires on its own)."""
    data = read_json(_pending_path(data_dir))
    if data is None:
        return False
    try:
        started = datetime.fromisoformat(str(data.get("started_at")))
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return (now or datetime.now(tz=UTC)) - started < PENDING_TTL


def login_command() -> list[str]:
    """OpenClaw's own device-code sign-in, run for the main agent through the running gateway."""
    return [
        "openclaw",
        "models",
        "auth",
        "login",
        "--provider",
        OPENAI_PROVIDER,
        "--device-code",
        "--agent",
        AUTH_AGENT_ID,
    ]


def login_interactive() -> int:
    """Run the sign-in in the owner's terminal; returns its exit code.

    The terminal is handed over as is: the link and the code OpenClaw shows are between the owner
    and OpenAI — nothing is captured or logged here.
    """
    return subprocess.call(login_command())  # noqa: S603 - fixed arguments


# --- putting the sign-in where it belongs (gateway stopped) -------------------------------------


def _ensure_shared_db(state_dir: Path) -> bool:
    """On a fresh container the shared database does not exist until OpenClaw first runs; an
    offline read command creates it with OpenClaw's own schema (never ours)."""
    if _shared_db(state_dir).is_file():
        return True
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603 - fixed arguments
            list(_INIT_STATE_COMMAND),
            capture_output=True,
            timeout=_INIT_STATE_TIMEOUT_S,
            check=False,
        )
    return _shared_db(state_dir).is_file()


def _state_db_shared(state_dir: Path) -> bool:
    """Whether OpenClaw reads — or, told so, will read — the shared auth store from the state database.

    Yes when ``auth.sharedStore`` says ``state-db``, or when the fact is missing and there is no legacy
    ``main`` store to keep reading from: OpenClaw's own condition for recording it on a fresh install's
    first shared write (:func:`_record_state_db_shared`). No when the shared store stays in that legacy
    store: the sign-in then belongs in the main agent's own store.
    """
    fact = _read_store(_shared_db(state_dir), _SHARED_SELECT, _SHARED_OWNERSHIP_KEY)
    if fact is not None:
        return fact.get("location") == _STATE_DB_LOCATION
    return not _legacy_main_db(state_dir).exists()


def _record_state_db_shared(state_dir: Path) -> None:
    """Record ``auth.sharedStore = {"location": "state-db"}`` unless a fact is there already. Called
    after the sign-in is written, so a store that could not take it never gets the fact alone."""
    _update_store(
        _shared_db(state_dir),
        _SHARED_SELECT,
        _SHARED_UPSERT,
        _SHARED_OWNERSHIP_KEY,
        lambda current: {"location": _STATE_DB_LOCATION} if current is None else None,
    )


def _keep_in_agent_store(state_dir: Path, profiles: ChatgptProfiles) -> bool:
    """The sign-in in the main agent's own store, out of the state database nobody reads it from."""
    if not _agent_db(state_dir).is_file():
        return False
    _update_agent(state_dir, lambda agent: _with_chatgpt(agent, profiles))
    if _shared_db(state_dir).is_file():
        _update_shared(state_dir, _drop_chatgpt)
    return True


def settle(data_dir: Path, state_dir: Path, *, connected: bool, pending: bool) -> str:
    """Put the ChatGPT sign-in where the agents read it — or take it away. The gateway must be stopped.

    - Connected: a fresh login in the main agent's own store moves to the shared store; with none
      there, the copy from the data volume goes back in (a container an update just replaced).
    - Otherwise (credits, or Claude) every ChatGPT profile and the copy are removed — the sign-out
      ``disconnect`` promises. Only a sign-in under way keeps the main agent's own store: that is
      where a fresh login lands before ``activate`` takes it.

    Every write reads the store in the same transaction and refuses one it cannot read, so our
    own provider keys next to the sign-in are never lost. Returns what it did, for the start log.
    """
    agent_db = _agent_db(state_dir)
    shared_db = _shared_db(state_dir)
    if connected:
        fresh = chatgpt_profiles(_read_store(agent_db, _AGENT_SELECT, _AGENT_STORE_KEY))
        if fresh:
            if not _ensure_shared_db(state_dir):
                return "shared-store-missing"
            if not _state_db_shared(state_dir):
                # OpenClaw shares another store: the main agent keeps the sign-in it got.
                snapshot(data_dir, state_dir)
                return "kept-in-agent-store"
            _update_shared(state_dir, lambda shared: _with_chatgpt(shared, fresh))
            _record_state_db_shared(state_dir)
            # Shared first: a failure in between leaves the sign-in twice, never nowhere.
            _update_agent(state_dir, _drop_chatgpt)
            snapshot(data_dir, state_dir)
            return "moved-to-shared"
        shared_now = chatgpt_profiles(_read_store(shared_db, _SHARED_SELECT, _SHARED_STORE_KEY))
        if shared_now:
            # Also a sign-in agent 4.3.0 moved here without the fact: OpenClaw reads it from now on.
            if not _state_db_shared(state_dir):
                return "moved-to-agent-store" if _keep_in_agent_store(state_dir, shared_now) else "unreadable-here"
            _record_state_db_shared(state_dir)
            snapshot(data_dir, state_dir)
            return "in-place"
        saved = load_snapshot(data_dir)
        if not saved:
            return "no-sign-in"
        if not _ensure_shared_db(state_dir):
            return "shared-store-missing"
        if not _state_db_shared(state_dir):
            return "restored-to-agent-store" if _keep_in_agent_store(state_dir, saved) else "unreadable-here"
        _update_shared(state_dir, lambda shared: _with_chatgpt(shared, saved))
        _record_state_db_shared(state_dir)
        return "restored"
    removed = shared_db.is_file() and _update_shared(state_dir, _drop_chatgpt)
    if not pending and agent_db.is_file():
        removed = _update_agent(state_dir, _drop_chatgpt) or removed
    forget(data_dir)
    if removed:
        return "signed-out"
    return "sign-in-under-way" if pending else "nothing-to-do"


def settle_for_choice(data_dir: Path, state_dir: Path) -> str:
    """:func:`settle` for what this machine is set to now."""
    choice = load_choice(data_dir)
    return settle(
        data_dir,
        state_dir,
        connected=choice is not None and choice.provider == "chatgpt",
        pending=login_pending(data_dir),
    )


#: How long ``settle`` waits for the previous gateway to exit: a restart signals it and starts the
#: next one without waiting, and a token it refreshes on its way out would be written over.
GATEWAY_STOP_WAIT_S = 30.0


#: Where the processes are listed (tests point it elsewhere: a host's ``/proc`` shows containers' too).
PROC_DIR = Path("/proc")


def gateway_running(proc: Path | None = None) -> bool:
    """An OpenClaw gateway process (``node openclaw.mjs gateway …``) is alive in this container."""
    proc = PROC_DIR if proc is None else proc
    if not proc.is_dir():
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"gateway" in args and any(arg.endswith(b"openclaw.mjs") for arg in args):
            return True
    return False


def wait_for_gateway_to_stop(timeout_s: float = GATEWAY_STOP_WAIT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while gateway_running():
        if time.monotonic() >= deadline:
            return False
        time.sleep(1.0)
    return True


def main(argv: list[str]) -> int:
    """``python -m sellerclaw_agent.subscription.chatgpt_auth settle`` — from ``openclaw_start``."""
    if argv[:1] != ["settle"]:
        print("usage: python -m sellerclaw_agent.subscription.chatgpt_auth settle", file=sys.stderr)  # noqa: T201
        return 2
    data_dir = Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    if not wait_for_gateway_to_stop():
        print("chatgpt sign-in: the previous gateway is still running, left as it is", file=sys.stderr)  # noqa: T201
        return 1
    try:
        outcome = settle_for_choice(data_dir, openclaw_state_dir())
    except (OSError, sqlite3.Error, StoreUnreadableError) as exc:
        print(f"chatgpt sign-in: could not settle ({type(exc).__name__})", file=sys.stderr)  # noqa: T201
        return 1
    print(f"chatgpt sign-in: {outcome}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
