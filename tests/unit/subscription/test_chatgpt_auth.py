"""The owner's ChatGPT sign-in: in the store every agent reads, kept across updates, gone on disconnect."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sellerclaw_agent.subscription import chatgpt_auth
from sellerclaw_agent.subscription.state import save_choice

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)

Factory = Callable[..., Any]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param("chatgpt-oauth", ["openai:default"], id="chatgpt-oauth"),
        pytest.param("among-our-keys", ["openai:me"], id="among-our-keys"),
        pytest.param("openai-api-key", [], id="openai-api-key-is-not-a-plan"),
        pytest.param("mid-refresh-marker", [], id="mid-refresh-marker"),
        pytest.param("other-provider", [], id="other-provider"),
    ],
)
def test_only_a_usable_chatgpt_sign_in_counts(
    make_chatgpt_profile: Factory, make_auth_store: Factory, our_keys: dict[str, Any], case: str, expected: list[str]
) -> None:
    profiles = {
        "chatgpt-oauth": {"openai:default": make_chatgpt_profile()},
        "among-our-keys": {**our_keys, "openai:me": make_chatgpt_profile()},
        "openai-api-key": {"openai:key": {"type": "api_key", "provider": "openai", "key": "sk"}},
        "mid-refresh-marker": {"openai:default": {"type": "oauth", "provider": "openai"}},
        "other-provider": {"anthropic:x": {**make_chatgpt_profile(), "provider": "anthropic"}},
    }[case]

    assert sorted(chatgpt_auth.chatgpt_profiles(make_auth_store(profiles)).profiles) == expected


def test_a_fresh_login_moves_to_the_store_every_agent_reads(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
) -> None:
    """The device-code login lands in the main agent's own store; specialists would never see it."""
    state = make_openclaw_state(
        shared=make_auth_store(our_keys),
        agent=make_auth_store({"openai:default": make_chatgpt_profile()}, lifetimes={"openai:default": "2026-12-01"}),
    )

    outcome = chatgpt_auth.settle(tmp_path, state.root, connected=True, pending=False)

    assert outcome == "moved-to-shared"
    assert state.shared() == make_auth_store(
        {**our_keys, "openai:default": make_chatgpt_profile()}, lifetimes={"openai:default": "2026-12-01"}
    )
    assert state.agent() == make_auth_store({}, lifetimes={})
    saved = json.loads(chatgpt_auth.snapshot_path(tmp_path).read_text(encoding="utf-8"))
    assert saved["profiles"] == {"openai:default": make_chatgpt_profile()}


@pytest.mark.parametrize(
    ("where", "outcome"),
    [
        pytest.param("fresh-login", "moved-to-shared", id="fresh-login"),
        # What 4.3.0 left behind: the sign-in moved to a store OpenClaw did not read.
        pytest.param("shared-already", "in-place", id="left-by-4.3.0"),
        pytest.param("copy-on-data-volume", "restored", id="after-an-update"),
    ],
)
@pytest.mark.parametrize(
    "ownership",
    [
        pytest.param(None, id="fresh-install-no-fact"),
        pytest.param({"location": "state-db"}, id="fact-already-recorded"),
    ],
)
def test_openclaw_is_told_to_read_the_shared_store_from_the_state_database(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
    where: str,
    outcome: str,
    ownership: dict[str, Any] | None,
) -> None:
    """Without ``auth.sharedStore = state-db`` OpenClaw reads the shared store from the implicit
    ``main`` agent — on a fresh container every turn on the plan failed: "No route-compatible
    authentication source is configured for openai"."""
    sign_in = {"openai:default": make_chatgpt_profile()}
    if where == "copy-on-data-volume":
        chatgpt_auth.snapshot(tmp_path, make_openclaw_state(name="before", shared=make_auth_store(sign_in)).root)
    state = make_openclaw_state(
        shared=make_auth_store({**our_keys, **(sign_in if where == "shared-already" else {})}),
        agent=make_auth_store(sign_in) if where == "fresh-login" else None,
        ownership=ownership,
    )

    assert chatgpt_auth.settle(tmp_path, state.root, connected=True, pending=False) == outcome

    assert state.ownership() == {"location": "state-db"}
    assert state.shared() == make_auth_store({**our_keys, **sign_in})


@pytest.mark.parametrize(
    ("ownership", "legacy_main"),
    [
        pytest.param({"location": "legacy-main"}, False, id="fact-says-legacy-main"),
        pytest.param(None, True, id="legacy-main-store-exists"),
    ],
)
def test_a_shared_store_kept_elsewhere_leaves_the_sign_in_with_the_main_agent(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
    ownership: dict[str, Any] | None,
    legacy_main: bool,
) -> None:
    """OpenClaw's own condition for relocating the shared store is not met: nothing is relocated,
    and the sign-in goes where the main agent reads it — never into a store nobody reads."""
    sign_in = {"openai:default": make_chatgpt_profile()}
    fresh = make_openclaw_state(
        shared=make_auth_store(our_keys), agent=make_auth_store(sign_in), ownership=ownership, legacy_main=legacy_main
    )
    leftover = make_openclaw_state(
        name="leftover",
        shared=make_auth_store({**our_keys, **sign_in}),
        agent=make_auth_store({}),
        ownership=ownership,
        legacy_main=legacy_main,
    )

    assert chatgpt_auth.settle(tmp_path, fresh.root, connected=True, pending=False) == "kept-in-agent-store"
    assert chatgpt_auth.settle(tmp_path, leftover.root, connected=True, pending=False) == "moved-to-agent-store"

    for state in (fresh, leftover):
        assert state.ownership() == ownership
        assert state.agent() == make_auth_store(sign_in)
        assert state.shared() == make_auth_store(our_keys)


def test_the_copy_on_the_data_volume_is_the_owners_alone(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
) -> None:
    state = make_openclaw_state(shared=make_auth_store({**our_keys, "openai:default": make_chatgpt_profile()}))

    assert chatgpt_auth.snapshot(tmp_path, state.root) is True

    path = chatgpt_auth.snapshot_path(tmp_path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # Only the ChatGPT sign-in: our own keys never leave OpenClaw's store.
    assert "sk-litellm" not in path.read_text(encoding="utf-8")


def test_a_replaced_container_gets_the_sign_in_back(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
) -> None:
    """``sellerclaw-agent update`` replaced OpenClaw's state; the copy on /data puts it back."""
    before = make_openclaw_state(
        shared=make_auth_store({**our_keys, "openai:default": make_chatgpt_profile(refresh="ref-7")})
    )
    chatgpt_auth.snapshot(tmp_path, before.root)
    fresh = make_openclaw_state(name="fresh", shared=make_auth_store(our_keys))

    outcome = chatgpt_auth.settle(tmp_path, fresh.root, connected=True, pending=False)

    assert outcome == "restored"
    assert fresh.shared() == make_auth_store({**our_keys, "openai:default": make_chatgpt_profile(refresh="ref-7")})


def test_the_copy_follows_a_refreshed_token(
    tmp_path: Path, make_openclaw_state: Factory, make_chatgpt_profile: Factory, make_auth_store: Factory
) -> None:
    """A restored older refresh token may already be spent, so the copy tracks every rotation."""
    state = make_openclaw_state(shared=make_auth_store({"openai:default": make_chatgpt_profile(refresh="ref-1")}))
    chatgpt_auth.snapshot(tmp_path, state.root)
    rotated = make_openclaw_state(
        name="rotated",
        shared=make_auth_store({"openai:default": make_chatgpt_profile(access="acc-2", refresh="ref-2")}),
    )

    chatgpt_auth.snapshot(tmp_path, rotated.root)

    assert chatgpt_auth.load_snapshot(tmp_path).profiles == {
        "openai:default": make_chatgpt_profile(access="acc-2", refresh="ref-2")
    }


def test_an_unchanged_sign_in_is_not_rewritten(
    tmp_path: Path, make_openclaw_state: Factory, make_chatgpt_profile: Factory, make_auth_store: Factory
) -> None:
    state = make_openclaw_state(shared=make_auth_store({"openai:default": make_chatgpt_profile()}))
    chatgpt_auth.snapshot(tmp_path, state.root)
    path = chatgpt_auth.snapshot_path(tmp_path)
    os.utime(path, (1, 1))

    chatgpt_auth.snapshot(tmp_path, state.root)

    assert path.stat().st_mtime == 1


def test_a_sign_in_already_shared_stays_where_it_is(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
) -> None:
    shared = make_auth_store({**our_keys, "openai:default": make_chatgpt_profile()})
    state = make_openclaw_state(shared=shared, agent=make_auth_store({}))

    assert chatgpt_auth.settle(tmp_path, state.root, connected=True, pending=False) == "in-place"
    assert state.shared() == shared
    assert chatgpt_auth.load_snapshot(tmp_path).profiles == {"openai:default": make_chatgpt_profile()}


def test_connected_without_any_sign_in_changes_nothing(
    tmp_path: Path, make_openclaw_state: Factory, make_auth_store: Factory, our_keys: dict[str, Any]
) -> None:
    """The monitor then reports the plan as signed out and the owner reconnects."""
    state = make_openclaw_state(shared=make_auth_store(our_keys), agent=make_auth_store({}))

    assert chatgpt_auth.settle(tmp_path, state.root, connected=True, pending=False) == "no-sign-in"
    assert state.shared() == make_auth_store(our_keys)


@pytest.mark.parametrize(
    ("database_appears", "expected"),
    [
        pytest.param(True, "restored", id="openclaw-creates-it"),
        pytest.param(False, "shared-store-missing", id="openclaw-could-not"),
    ],
)
def test_a_brand_new_state_gets_its_database_from_openclaw_first(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    database_appears: bool,
    expected: str,
) -> None:
    """Never our own schema: an offline OpenClaw read creates the database, then the sign-in goes in."""
    signed_in = make_openclaw_state(shared=make_auth_store({"openai:default": make_chatgpt_profile()}))
    chatgpt_auth.snapshot(tmp_path, signed_in.root)
    empty = make_openclaw_state(name="empty", empty=True)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if database_appears:
            empty.create_shared(make_auth_store(our_keys))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(chatgpt_auth.subprocess, "run", fake_run)

    assert chatgpt_auth.settle(tmp_path, empty.root, connected=True, pending=False) == expected
    assert calls == [["openclaw", "models", "auth", "list", "--provider", "openai"]]
    if database_appears:
        assert empty.shared() == make_auth_store({**our_keys, "openai:default": make_chatgpt_profile()})


def test_disconnecting_removes_the_sign_in_everywhere_and_keeps_our_keys(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
) -> None:
    state = make_openclaw_state(
        shared=make_auth_store({**our_keys, "openai:default": make_chatgpt_profile()}, lifetimes={"openai:default": "x"}),
        agent=make_auth_store({"openai:stale": {"type": "oauth", "provider": "openai"}}),
    )
    chatgpt_auth.snapshot(tmp_path, state.root)

    outcome = chatgpt_auth.settle(tmp_path, state.root, connected=False, pending=False)

    assert outcome == "signed-out"
    assert state.shared() == make_auth_store(our_keys, lifetimes={})
    assert state.agent() == make_auth_store({})
    assert not chatgpt_auth.snapshot_path(tmp_path).exists()


def test_a_sign_in_under_way_is_left_alone(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
) -> None:
    fresh_login = make_auth_store({"openai:default": make_chatgpt_profile()})
    state = make_openclaw_state(shared=make_auth_store(our_keys), agent=fresh_login)

    assert chatgpt_auth.settle(tmp_path, state.root, connected=False, pending=True) == "sign-in-under-way"
    assert state.agent() == fresh_login


def test_disconnecting_during_a_sign_in_still_removes_the_shared_one(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
) -> None:
    """An abandoned ``connect chatgpt`` leaves its marker for 20 minutes; ``disconnect`` meanwhile
    must still take the working sign-in away — only the fresh login's own store waits."""
    fresh_login = make_auth_store({"openai:default": make_chatgpt_profile(access="acc-2", refresh="ref-2")})
    state = make_openclaw_state(
        shared=make_auth_store({**our_keys, "openai:default": make_chatgpt_profile()}), agent=fresh_login
    )
    chatgpt_auth.snapshot(tmp_path, state.root)

    outcome = chatgpt_auth.settle(tmp_path, state.root, connected=False, pending=True)

    assert outcome == "signed-out"
    assert state.shared() == make_auth_store(our_keys)
    assert state.agent() == fresh_login
    assert not chatgpt_auth.snapshot_path(tmp_path).exists()


@pytest.mark.parametrize("connected", [pytest.param(True, id="moving-in"), pytest.param(False, id="signing-out")])
def test_a_shared_store_it_cannot_read_is_never_written_over(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    connected: bool,
) -> None:
    """Taking an unreadable store for an empty one would write the sign-in over our provider keys."""
    state = make_openclaw_state(agent=make_auth_store({"openai:default": make_chatgpt_profile()}))
    with sqlite3.connect(state.shared_db) as conn:
        conn.execute("INSERT INTO config_machine_state VALUES ('authProfiles.store', '{not json', 1)")

    with pytest.raises(chatgpt_auth.StoreUnreadableError, match="authProfiles.store is not JSON"):
        chatgpt_auth.settle(tmp_path, state.root, connected=connected, pending=False)

    with sqlite3.connect(state.shared_db) as conn:
        assert conn.execute("SELECT value_json FROM config_machine_state").fetchall() == [("{not json",)]
    assert chatgpt_auth.chatgpt_profiles(state.agent())  # the fresh login is still where it landed


def test_the_start_script_reports_an_unreadable_store_and_writes_nothing(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = make_openclaw_state(agent=make_auth_store({"openai:default": make_chatgpt_profile()}))
    with sqlite3.connect(state.shared_db) as conn:
        conn.execute("INSERT INTO config_machine_state VALUES ('authProfiles.store', '[]', 1)")
    save_choice(tmp_path, "chatgpt")
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state.root))

    assert chatgpt_auth.main(["settle"]) == 1
    assert "could not settle (StoreUnreadableError)" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("minutes_ago", "pending"),
    [
        pytest.param(5, True, id="just-started"),
        pytest.param(int(chatgpt_auth.PENDING_TTL.total_seconds() // 60), False, id="abandoned"),
    ],
)
def test_a_sign_in_nobody_finishes_expires(tmp_path: Path, minutes_ago: int, pending: bool) -> None:
    chatgpt_auth.mark_login_pending(tmp_path, now=NOW - timedelta(minutes=minutes_ago))

    assert chatgpt_auth.login_pending(tmp_path, now=NOW) is pending


def test_a_finished_or_cancelled_sign_in_is_no_longer_pending(tmp_path: Path) -> None:
    chatgpt_auth.mark_login_pending(tmp_path, now=NOW)

    chatgpt_auth.clear_login_pending(tmp_path)

    assert chatgpt_auth.login_pending(tmp_path, now=NOW) is False


def test_the_sign_in_is_openclaws_own_device_code_login_for_the_main_agent() -> None:
    assert chatgpt_auth.login_command() == [
        "openclaw",
        "models",
        "auth",
        "login",
        "--provider",
        "openai",
        "--device-code",
        "--agent",
        "supervisor",
    ]


@pytest.mark.parametrize(
    ("choice", "expected_line"),
    [
        pytest.param("chatgpt", "chatgpt sign-in: moved-to-shared", id="connected"),
        pytest.param(None, "chatgpt sign-in: signed-out", id="on-credits"),
    ],
)
def test_the_start_script_settles_for_what_the_machine_is_set_to(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    choice: str | None,
    expected_line: str,
) -> None:
    state = make_openclaw_state(
        shared=make_auth_store(our_keys), agent=make_auth_store({"openai:default": make_chatgpt_profile()})
    )
    data_dir = tmp_path / "data"
    if choice is not None:
        save_choice(data_dir, choice)
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state.root))

    assert chatgpt_auth.main(["settle"]) == 0
    assert capsys.readouterr().out.strip() == expected_line


def test_the_start_script_refuses_anything_else() -> None:
    assert chatgpt_auth.main(["restore"]) == 2


def _process(proc: Path, pid: int, *args: str) -> None:
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")


@pytest.mark.parametrize(
    ("args", "running"),
    [
        pytest.param(("node", "openclaw.mjs", "gateway", "--bind", "loopback"), True, id="gateway"),
        pytest.param(("node", "/app/openclaw.mjs", "gateway"), True, id="gateway-by-full-path"),
        pytest.param(("node", "openclaw.mjs", "models", "auth", "list"), False, id="another-openclaw-command"),
        pytest.param(("bash", "/app/runtime/commands/openclaw_start"), False, id="the-start-script"),
    ],
)
def test_only_a_gateway_process_counts_as_running(tmp_path: Path, args: tuple[str, ...], running: bool) -> None:
    _process(tmp_path, 41, *args)
    (tmp_path / "self").mkdir()

    assert chatgpt_auth.gateway_running(tmp_path) is running


def test_settle_waits_for_the_previous_gateway_to_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A restart signals the old gateway and starts the next at once; settling under a gateway
    that still writes would lose the token it refreshed on its way out."""
    alive = iter([True, True, False])
    slept: list[float] = []
    monkeypatch.setattr(chatgpt_auth, "gateway_running", lambda: next(alive))
    monkeypatch.setattr(chatgpt_auth.time, "sleep", slept.append)

    assert chatgpt_auth.wait_for_gateway_to_stop(timeout_s=30) is True
    assert slept == [1.0, 1.0]


def test_a_gateway_that_does_not_exit_leaves_the_sign_in_alone(
    tmp_path: Path,
    make_openclaw_state: Factory,
    make_chatgpt_profile: Factory,
    make_auth_store: Factory,
    our_keys: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fresh_login = make_auth_store({"openai:default": make_chatgpt_profile()})
    state = make_openclaw_state(shared=make_auth_store(our_keys), agent=fresh_login)
    save_choice(tmp_path, "chatgpt")
    monkeypatch.setenv("SELLERCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state.root))
    monkeypatch.setattr(chatgpt_auth, "wait_for_gateway_to_stop", lambda: False)

    assert chatgpt_auth.main(["settle"]) == 1
    assert "the previous gateway is still running, left as it is" in capsys.readouterr().err
    assert (state.agent(), state.shared()) == (fresh_login, make_auth_store(our_keys))
