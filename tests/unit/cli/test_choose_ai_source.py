""""What should power your agent?" — after sign-in (``./setup.sh``, the installer) and ``connect`` alone.

On credits it makes the case for the owner's own plan; on a plan it offers to keep it, switch,
sign in again or go back to credits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from sellerclaw_agent import cli, cli_subscription
from sellerclaw_agent.subscription import claude_cli

pytestmark = pytest.mark.unit


@pytest.fixture()
def console() -> Console:
    return Console(theme=cli._THEME, record=True, width=200)


class _Owner:
    """Answers the question and records what the agent was asked to do."""

    def __init__(self, answer: str = "keep", *, exit_code: int = 0) -> None:
        self.answer = answer
        self.exit_code = exit_code
        self.asked: list[tuple[list[str], str | None]] = []
        self.connected: list[list[str]] = []
        self.disconnected = 0

    def select(
        self, console: Console, title: str, options: list[tuple[str, str]], *, default_value: str | None = None
    ) -> str:
        self.asked.append(([value for value, _label in options], default_value))
        return self.answer

    def connect(self, console: Console, argv: list[str]) -> int:
        self.connected.append(argv)
        return self.exit_code

    def disconnect(self, console: Console) -> int:
        self.disconnected += 1
        return self.exit_code


def _wire(
    monkeypatch: pytest.MonkeyPatch, owner: _Owner, *, state: dict[str, Any] | None, tty: bool = True
) -> None:
    monkeypatch.setattr(cli, "_installed_from_repo", lambda: True)
    monkeypatch.setattr(cli, "_select_option", owner.select)
    monkeypatch.setattr(cli_subscription, "cmd_connect", owner.connect)
    monkeypatch.setattr(cli_subscription, "cmd_disconnect", owner.disconnect)
    monkeypatch.setattr(cli_subscription, "ai_state", lambda: state)
    monkeypatch.setattr(cli_subscription.sys.stdin, "isatty", lambda: tty)


def _text(console: Console) -> str:
    return " ".join(console.export_text().split())


_ON_CREDITS: dict[str, Any] = {"provider": None, "offered": False, "status": None}
_ON_CLAUDE: dict[str, Any] = {"provider": "claude", "offered": True, "status": {"state": "connected", "plan": "max"}}
_CLAUDE_SIGNED_OUT: dict[str, Any] = {"provider": "claude", "offered": True, "status": {"state": "signed_out"}}


@pytest.mark.parametrize(
    ("state", "options", "default"),
    [
        pytest.param(_ON_CREDITS, ["credits", "chatgpt", "claude"], "credits", id="on-credits"),
        pytest.param(_ON_CLAUDE, ["keep", "chatgpt", "again", "credits"], "keep", id="on-a-plan"),
        pytest.param(
            {"provider": "chatgpt", "offered": True, "status": {"state": "limit_reached"}},
            ["keep", "claude", "again", "credits"],
            "keep",
            id="limit-used-up-keeps-by-default",
        ),
        pytest.param(_CLAUDE_SIGNED_OUT, ["again", "chatgpt", "credits", "keep"], "again", id="sign-in-ended"),
    ],
)
def test_the_answers_fit_where_the_agent_stands(state: dict[str, Any], options: list[str], default: str) -> None:
    choices, chosen_by_default = cli_subscription.ai_source_choices(state)

    assert ([value for value, _label in choices], chosen_by_default) == (options, default)


def test_on_a_plan_the_answers_name_it() -> None:
    choices, _ = cli_subscription.ai_source_choices(_ON_CLAUDE)

    assert choices == [
        ("keep", "Keep my Claude plan"),
        ("chatgpt", "Switch to my ChatGPT plan (Plus or Pro)"),
        ("again", "Sign in to Claude again"),
        ("credits", "Switch to SellerClaw credits"),
    ]


@pytest.mark.parametrize(
    ("state", "answer", "exit_code", "connected", "disconnected", "message"),
    [
        pytest.param(
            _ON_CREDITS, "credits", 0, [], 0,
            "Your agent runs on SellerClaw credits. Switch any time: ./setup.sh connect",
            id="credits-kept",
        ),
        pytest.param(_ON_CREDITS, "chatgpt", 0, [["chatgpt"]], 0, None, id="credits-to-chatgpt"),
        pytest.param(
            _ON_CREDITS, "claude", 1, [["claude"]], 0,
            "Your Claude plan was not connected. The agent runs on SellerClaw credits. Try again any time: "
            "./setup.sh connect",
            id="credits-sign-in-did-not-finish",
        ),
        pytest.param(_ON_CLAUDE, "keep", 0, [], 0, None, id="plan-kept"),
        pytest.param(_ON_CLAUDE, "chatgpt", 0, [["chatgpt"]], 0, None, id="plan-switched"),
        pytest.param(
            _ON_CLAUDE, "chatgpt", 1, [["chatgpt"]], 0,
            "Your ChatGPT plan was not connected. Your agent keeps your Claude plan. Try again any time: "
            "./setup.sh connect",
            id="switch-did-not-finish",
        ),
        pytest.param(_ON_CLAUDE, "again", 0, [["claude", "--sign-in-again"]], 0, None, id="signed-in-again"),
        pytest.param(
            _CLAUDE_SIGNED_OUT, "again", 1, [["claude", "--sign-in-again"]], 0,
            "Claude was not signed in again. Try again any time: ./setup.sh connect",
            id="sign-in-again-did-not-finish",
        ),
        pytest.param(_ON_CLAUDE, "credits", 0, [], 1, None, id="back-to-credits"),
    ],
)
def test_the_answer_is_carried_out(  # noqa: PLR0913
    monkeypatch: pytest.MonkeyPatch,
    console: Console,
    state: dict[str, Any],
    answer: str,
    exit_code: int,
    connected: list[list[str]],
    disconnected: int,
    message: str | None,
) -> None:
    owner = _Owner(answer, exit_code=exit_code)
    _wire(monkeypatch, owner, state=state)

    assert cli_subscription.choose_ai_source(console) == exit_code
    assert (owner.connected, owner.disconnected) == (connected, disconnected)
    if message is not None:
        assert message in _text(console)


@pytest.mark.parametrize(
    ("state", "shown", "not_shown"),
    [
        pytest.param(_ON_CREDITS, "every effort level is yours, Max included", "Your agent runs on", id="credits-pitch"),
        pytest.param(_ON_CLAUDE, "Your agent runs on your Claude plan (Max).", "Already pay for", id="plan-stated"),
        pytest.param(
            _CLAUDE_SIGNED_OUT,
            "Your agent runs on your Claude plan, but the Claude sign-in has ended — it is waiting.",
            "Already pay for",
            id="sign-in-ended-stated",
        ),
    ],
)
def test_the_question_starts_from_where_the_agent_stands(
    monkeypatch: pytest.MonkeyPatch, console: Console, state: dict[str, Any], shown: str, not_shown: str
) -> None:
    _wire(monkeypatch, _Owner("keep"), state=state)

    cli_subscription.choose_ai_source(console)

    assert shown in _text(console)
    assert not_shown not in _text(console)


@pytest.mark.parametrize(
    ("state", "tty"),
    [
        pytest.param(None, True, id="agent-cannot-say"),
        pytest.param(_ON_CREDITS, False, id="nobody-at-the-terminal"),
    ],
)
def test_nothing_is_asked_without_a_choice_to_make(
    monkeypatch: pytest.MonkeyPatch, console: Console, state: dict[str, Any] | None, tty: bool
) -> None:
    owner = _Owner("chatgpt")
    _wire(monkeypatch, owner, state=state, tty=tty)

    assert cli_subscription.choose_ai_source(console) == 0
    assert (owner.asked, owner.connected, owner.disconnected) == ([], [], 0)
    assert console.export_text() == ""


@pytest.mark.parametrize(
    ("tty", "exit_code", "asked"),
    [
        pytest.param(True, 0, True, id="asks-at-a-terminal"),
        pytest.param(False, 2, False, id="names-the-plans-otherwise"),
    ],
)
def test_connect_without_a_plan_asks(
    monkeypatch: pytest.MonkeyPatch, console: Console, tty: bool, exit_code: int, asked: bool
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(cli_subscription.sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr(cli_subscription, "choose_ai_source", lambda console: calls.append(1) or 0)

    assert cli_subscription.cmd_connect(console, []) == exit_code
    assert bool(calls) is asked
    if not asked:
        assert "Choose what to connect: sellerclaw-agent connect claude | chatgpt" in _text(console)


@pytest.mark.parametrize(
    ("argv", "signed_in"),
    [
        pytest.param(["claude"], False, id="a-working-sign-in-is-kept"),
        pytest.param(["claude", "--sign-in-again"], True, id="sign-in-again-asks-anew"),
    ],
)
def test_claude_signs_in_again_only_when_asked(
    monkeypatch: pytest.MonkeyPatch, console: Console, tmp_path: Path, argv: list[str], signed_in: bool
) -> None:
    logins: list[Path] = []
    activated: list[str] = []
    monkeypatch.setattr(cli_subscription, "_agent_reachable", lambda console: True)
    monkeypatch.setattr(cli_subscription, "_runs_on_host", lambda: False)
    monkeypatch.setattr(cli_subscription, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cli_subscription.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(claude_cli, "is_installed", lambda data_dir: True)
    monkeypatch.setattr(
        claude_cli,
        "auth_status",
        lambda data_dir: claude_cli.ClaudeAuthStatus(logged_in=True, auth_method="claude.ai", subscription_type="max"),
    )
    monkeypatch.setattr(claude_cli, "login_interactive", lambda data_dir: logins.append(data_dir) or 0)
    monkeypatch.setattr(
        cli_subscription, "_call", lambda method, path, **_: activated.append(path) or {"offered": True}
    )

    assert cli_subscription.cmd_connect(console, argv) == 0
    assert logins == ([tmp_path] if signed_in else [])
    assert activated == ["/ai-subscription/claude/activate"]
    assert "Your agent now runs on your Claude plan (Max)." in _text(console)


@pytest.mark.parametrize(
    ("login_exit", "after", "exit_code", "message"),
    [
        pytest.param(
            1, claude_cli.ClaudeAuthStatus(logged_in=True, auth_method="claude.ai", subscription_type="max"), 1,
            "The new Claude sign-in did not finish. Your agent keeps the one it had.",
            id="cancelled-keeps-the-old-sign-in",
        ),
        pytest.param(
            0, claude_cli.ClaudeAuthStatus(logged_in=True, auth_method="console"), 1,
            "Claude is now signed in without a Claude plan, so your agent cannot think on it. "
            "Sign in with the Claude account that has Pro or Max: ./setup.sh connect claude --sign-in-again",
            id="an-account-without-a-plan-is-said-so",
        ),
    ],
)
def test_signing_in_to_claude_again_never_claims_a_switch_it_did_not_make(
    monkeypatch: pytest.MonkeyPatch,
    console: Console,
    tmp_path: Path,
    login_exit: int,
    after: claude_cli.ClaudeAuthStatus,
    exit_code: int,
    message: str,
) -> None:
    statuses = iter(
        [claude_cli.ClaudeAuthStatus(logged_in=True, auth_method="claude.ai", subscription_type="max"), after]
    )
    activated: list[str] = []
    monkeypatch.setattr(cli, "_installed_from_repo", lambda: True)
    monkeypatch.setattr(cli_subscription, "_agent_reachable", lambda console: True)
    monkeypatch.setattr(cli_subscription, "_runs_on_host", lambda: False)
    monkeypatch.setattr(cli_subscription, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cli_subscription.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(claude_cli, "is_installed", lambda data_dir: True)
    monkeypatch.setattr(claude_cli, "auth_status", lambda data_dir: next(statuses))
    monkeypatch.setattr(claude_cli, "login_interactive", lambda data_dir: login_exit)
    monkeypatch.setattr(cli_subscription, "_call", lambda method, path, **_: activated.append(path) or {})

    assert cli_subscription.cmd_connect(console, ["claude", "--sign-in-again"]) == exit_code
    assert activated == []
    assert message in _text(console)
