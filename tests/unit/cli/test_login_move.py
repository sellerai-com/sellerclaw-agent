"""``sellerclaw-agent login``: once signed in, make sure this computer is where the agent runs.

When the owner's agent runs on a SellerClaw cloud machine the cloud refuses this one with
``agent_runs_in_cloud``; at a terminal the CLI offers the move, without one it says how to move it
later. ``agent_move_in_progress`` (a move started from the web panel) is simply waited out.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from rich.console import Console
from sellerclaw_agent import cli

pytestmark = pytest.mark.unit


def _console() -> Console:
    """A recording console with the CLI's own theme (its ``[hint]`` / ``[label]`` styles)."""
    return Console(record=True, width=200, theme=cli._THEME)


@pytest.fixture(autouse=True)
def _local_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SELLERCLAW_LOCAL_API_KEY", "cli-test-local-key")
    cli._clear_local_control_plane_key_cache()
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    yield
    cli._clear_local_control_plane_key_cache()


@pytest.fixture()
def make_health_snapshot() -> Callable[..., dict[str, Any]]:
    """``/health`` as the CLI reads it: the session, and the ping loop's success / last refusal."""

    def _factory(*, connected: bool = False, error_code: str | None = None, error: str | None = None) -> dict[str, Any]:
        return {
            "session": {"connected": connected},
            "tasks": {
                "ping_loop": {
                    "last_success_at": "2026-11-14T10:00:00+00:00" if connected else None,
                    "last_error": error or (f"refused: {error_code}" if error_code else None),
                    "last_error_code": error_code,
                },
                "chat_sse": {"connected": connected},
            },
        }

    return _factory


class _Health:
    """Serves snapshots in order; the last one repeats."""

    def __init__(self, *snapshots: dict[str, Any]) -> None:
        self.snapshots = list(snapshots)
        self.reads = 0

    def __call__(self, _base_url: str) -> dict[str, Any]:
        self.reads += 1
        return self.snapshots[min(self.reads, len(self.snapshots)) - 1]


@pytest.fixture()
def owner(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """Who is at the terminal and what they answer; returns the questions asked."""

    def _install(*, tty: bool, answer: bool = True) -> list[str]:
        asked: list[str] = []
        monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)

        def _confirm(_console: Console, question: str, *, default: bool = True) -> bool:
            asked.append(question)
            return answer

        monkeypatch.setattr(cli, "_confirm", _confirm)
        return asked

    return _install


@pytest.fixture()
def move_requests(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """Stub the local ``POST /hosting/move-here``; returns the list of calls made."""

    def _install(*, answer: str = "moving", error: str | None = None) -> list[str]:
        calls: list[str] = []

        def _request(base_url: str) -> str:
            calls.append(base_url)
            if error is not None:
                raise RuntimeError(error)
            return answer

        monkeypatch.setattr(cli, "_request_move_here", _request)
        return calls

    return _install


def test_connected_right_away_runs_here(monkeypatch: pytest.MonkeyPatch, make_health_snapshot, owner) -> None:
    asked = owner(tty=True)
    monkeypatch.setattr(cli, "_get_health_snapshot", _Health(make_health_snapshot(connected=True)))

    place, chat_ok = cli._connect_here(_console(), "http://agent")

    assert (place, chat_ok) == ("here", True)
    assert asked == []


def test_no_terminal_keeps_the_agent_in_the_cloud(
    monkeypatch: pytest.MonkeyPatch, make_health_snapshot, owner, move_requests
) -> None:
    asked = owner(tty=False)
    calls = move_requests()
    monkeypatch.setattr(cli, "_get_health_snapshot", _Health(make_health_snapshot(error_code=cli.RUNS_IN_CLOUD)))
    console = _console()

    place, _chat = cli._connect_here(console, "http://agent")

    assert place == "cloud"
    assert asked == [] and calls == []
    out = console.export_text()
    assert "Your agent runs in SellerClaw's cloud right now." in out
    assert 'Your agent keeps running in the cloud. To move it here later: open Hosting in SellerClaw and press "Move to your computer".' in out


def test_the_owner_says_no(monkeypatch: pytest.MonkeyPatch, make_health_snapshot, owner, move_requests) -> None:
    asked = owner(tty=True, answer=False)
    calls = move_requests()
    monkeypatch.setattr(cli, "_get_health_snapshot", _Health(make_health_snapshot(error_code=cli.RUNS_IN_CLOUD)))

    place, _chat = cli._connect_here(_console(), "http://agent")

    assert place == "cloud"
    assert asked == ["Move it to this computer?"]
    assert calls == []


@pytest.mark.parametrize("answer", ["moving", "moved", "already_here"])
def test_the_owner_says_yes_and_this_computer_takes_over(
    monkeypatch: pytest.MonkeyPatch, make_health_snapshot, owner, move_requests, answer: str
) -> None:
    owner(tty=True, answer=True)
    calls = move_requests(answer=answer)
    health = _Health(
        make_health_snapshot(error_code=cli.RUNS_IN_CLOUD),
        # Asked to move: the cloud machine hands over, then this computer is let in.
        make_health_snapshot(error_code=cli.MOVE_IN_PROGRESS),
        make_health_snapshot(connected=True),
    )
    monkeypatch.setattr(cli, "_get_health_snapshot", health)
    console = _console()

    place, chat_ok = cli._connect_here(console, "http://agent")

    assert (place, chat_ok) == ("here", True)
    assert calls == ["http://agent"]
    assert "Your agent runs on this computer now." in console.export_text()


def test_a_refused_move_leaves_the_agent_in_the_cloud(
    monkeypatch: pytest.MonkeyPatch, make_health_snapshot, owner, move_requests
) -> None:
    owner(tty=True, answer=True)
    move_requests(error="The cloud machine is still starting. Try again in a minute.")
    monkeypatch.setattr(cli, "_get_health_snapshot", _Health(make_health_snapshot(error_code=cli.RUNS_IN_CLOUD)))
    console = _console()

    place, _chat = cli._connect_here(console, "http://agent")

    assert place == "cloud"
    out = console.export_text()
    assert "Could not move your agent here: The cloud machine is still starting. Try again in a minute." in out
    assert 'press "Move to your computer"' in out


def test_a_move_started_on_the_web_is_waited_out(
    monkeypatch: pytest.MonkeyPatch, make_health_snapshot, owner, move_requests
) -> None:
    """The 45 s wait grows to the hand-over's few minutes once the cloud says a move is under way."""
    asked = owner(tty=True)
    calls = move_requests()
    clock = {"now": 0.0}

    def _monotonic() -> float:
        clock["now"] += 10.0  # every poll costs ten seconds
        return clock["now"]

    monkeypatch.setattr(cli.time, "monotonic", _monotonic)
    health = _Health(*([make_health_snapshot(error_code=cli.MOVE_IN_PROGRESS)] * 8), make_health_snapshot(connected=True))
    monkeypatch.setattr(cli, "_get_health_snapshot", health)

    place, _chat = cli._connect_here(_console(), "http://agent")

    assert place == "here"
    assert health.reads == 9, "kept waiting well past the usual 45 seconds"
    assert asked == [] and calls == []


def test_a_hand_over_that_never_comes_says_so(
    monkeypatch: pytest.MonkeyPatch, make_health_snapshot, owner, move_requests
) -> None:
    owner(tty=True)
    move_requests()
    clock = {"now": 0.0}

    def _monotonic() -> float:
        clock["now"] += 30.0
        return clock["now"]

    monkeypatch.setattr(cli.time, "monotonic", _monotonic)
    monkeypatch.setattr(cli, "_installed_from_repo", lambda: False)
    monkeypatch.setattr(cli, "_get_health_snapshot", _Health(make_health_snapshot(error_code=cli.MOVE_IN_PROGRESS)))
    console = _console()

    place, _chat = cli._connect_here(console, "http://agent")

    assert place == "moving", "a hand-over that outlasts the wait goes on by itself — not a failure"
    out = console.export_text()
    assert "Your agent is still moving to this computer." in out
    assert "Check in a minute: sellerclaw-agent status" in out
    assert "Step failed" not in out


@pytest.mark.parametrize(
    "first",
    [
        pytest.param("asked-here", id="the-owner-said-yes-here"),
        pytest.param("started-on-the-web", id="started-on-the-web"),
    ],
)
def test_a_move_whose_last_knock_failed_on_the_network_is_still_moving(
    monkeypatch: pytest.MonkeyPatch, make_health_snapshot, owner, move_requests, first: str
) -> None:
    """The cloud hands over on its own; a knock lost on the way says nothing about the move."""
    owner(tty=True, answer=True)
    move_requests()
    clock = {"now": 0.0}

    def _monotonic() -> float:
        clock["now"] += 30.0
        return clock["now"]

    monkeypatch.setattr(cli.time, "monotonic", _monotonic)
    opening = cli.RUNS_IN_CLOUD if first == "asked-here" else cli.MOVE_IN_PROGRESS
    health = _Health(
        make_health_snapshot(error_code=opening),
        make_health_snapshot(error_code=cli.MOVE_IN_PROGRESS),
        # From here on the knocks time out: the last error has no code.
        make_health_snapshot(error="timed out"),
    )
    monkeypatch.setattr(cli, "_get_health_snapshot", health)
    console = _console()

    place, _chat = cli._connect_here(console, "http://agent")

    assert place == "moving"
    out = console.export_text()
    assert "Your agent is still moving to this computer." in out
    assert "Check your internet connection" not in out


@pytest.mark.parametrize(
    ("from_repo", "expected", "unexpected"),
    [
        pytest.param(False, ["sellerclaw-agent logs", "sellerclaw-agent status", "sellerclaw-agent login"], "./setup.sh", id="installed"),
        pytest.param(True, ["docker compose logs server", "./setup.sh status", "./setup.sh login"], "sellerclaw-agent", id="checkout"),
    ],
)
def test_a_connection_that_never_comes_up_gives_hints_for_this_install(
    monkeypatch: pytest.MonkeyPatch,
    make_health_snapshot,
    owner,
    from_repo: bool,
    expected: list[str],
    unexpected: str,
) -> None:
    owner(tty=True)
    clock = {"now": 0.0}

    def _monotonic() -> float:
        clock["now"] += 30.0
        return clock["now"]

    monkeypatch.setattr(cli.time, "monotonic", _monotonic)
    monkeypatch.setattr(cli, "_installed_from_repo", lambda: from_repo)
    monkeypatch.setattr(cli, "_get_health_snapshot", _Health(make_health_snapshot(error="cloud 502 bad gateway")))
    console = _console()

    place, _chat = cli._connect_here(console, "http://agent")

    assert place == "failed"
    out = console.export_text()
    assert "cloud 502 bad gateway" in out
    for hint in expected:
        assert hint in out
    assert unexpected not in out


@pytest.mark.parametrize(
    ("place", "expected_exit", "prints_status"),
    [
        pytest.param(("here", True), 0, True, id="runs-here"),
        pytest.param(("cloud", False), cli.EXIT_RUNS_IN_CLOUD, False, id="stays-in-the-cloud"),
        pytest.param(("moving", False), cli.EXIT_MOVE_CONTINUES, False, id="still-moving"),
        pytest.param(("failed", False), 1, False, id="failed"),
    ],
)
@pytest.mark.parametrize("command", ["login", "where"])
def test_login_and_where_exit_codes_say_where_the_agent_runs(
    monkeypatch: pytest.MonkeyPatch,
    place: tuple[str, bool],
    expected_exit: int,
    prints_status: bool,
    command: str,
) -> None:
    signed_in: list[bool] = []
    statuses: list[str] = []
    monkeypatch.setattr(cli, "wait_for_agent", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "_interactive_auth", lambda *_a, **_k: signed_in.append(True))
    monkeypatch.setattr(cli, "_connect_here", lambda _c, _b: place)
    monkeypatch.setattr(cli, "_print_status", lambda _c, base: statuses.append(base) or 0)

    console = _console()
    code = cli.cmd_login(console, browser_only=True) if command == "login" else cli.cmd_where(console)

    assert code == expected_exit
    assert signed_in == ([True] if command == "login" else []), "`where` never signs in again"
    assert statuses == (["http://127.0.0.1:8001"] if prints_status else [])


def test_main_routes_where(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "cmd_where", lambda _console: cli.EXIT_RUNS_IN_CLOUD)
    monkeypatch.setattr(sys, "argv", ["sellerclaw-agent", "where"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == cli.EXIT_RUNS_IN_CLOUD


def test_where_is_not_listed_in_help() -> None:
    console = _console()

    cli.cmd_help(console)

    assert "where" not in console.export_text().split()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        pytest.param(cli.RUNS_IN_CLOUD, "Your agent runs in SellerClaw's cloud right now — this computer stands by.", id="cloud"),
        pytest.param(cli.MOVE_IN_PROGRESS, "Taking over from the cloud machine…", id="moving"),
    ],
)
def test_status_says_when_this_computer_is_not_the_agent(code: str, expected: str) -> None:
    console = _console()

    console.print(cli._place_status_line(code))

    assert expected in console.export_text()


@pytest.mark.parametrize("code", [None, "agent_connection_already_active"])
def test_status_adds_nothing_otherwise(code: str | None) -> None:
    assert cli._place_status_line(code) == ""


def _serve(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    monkeypatch.setattr(
        cli, "sync_client", lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), timeout=kw.get("timeout"))
    )
    return seen


def test_request_move_here_calls_the_local_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, httpx.Response(200, json={"status": "moving"}))

    assert cli._request_move_here("http://agent") == "moving"
    [request] = seen
    assert (request.method, str(request.url)) == ("POST", "http://agent/hosting/move-here")
    assert request.headers["authorization"] == "Bearer cli-test-local-key"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        pytest.param(
            httpx.Response(409, json={"detail": {"code": "hosting_transition_in_flight", "message": "Still starting."}}),
            "Still starting.",
            id="refused",
        ),
        pytest.param(httpx.Response(200, text="not json"), "Invalid answer from the agent.", id="garbage"),
    ],
)
def test_request_move_here_failures_carry_the_reason(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, message: str
) -> None:
    _serve(monkeypatch, response)

    with pytest.raises(RuntimeError, match=message):
        cli._request_move_here("http://agent")


def test_logs_hint_follows_the_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "agent_root", lambda: tmp_path)
    assert cli._logs_hint() == "sellerclaw-agent logs"
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    assert cli._logs_hint() == "docker compose logs server"
