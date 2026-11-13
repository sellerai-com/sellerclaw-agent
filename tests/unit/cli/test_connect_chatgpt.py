"""``sellerclaw-agent connect chatgpt``: ready the agent, OpenClaw's own sign-in, then switch — or undo."""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console
from sellerclaw_agent import cli, cli_subscription
from sellerclaw_agent.subscription import chatgpt_auth

pytestmark = pytest.mark.unit


class _Agent:
    """The local agent API as the command sees it: records each call, answers or refuses.

    ``starts_after`` — how many health checks report OpenClaw still starting (``None``: never starts).
    """

    def __init__(
        self, *, refuse: str | None = None, starts_after: int | None = 0, errors: dict[str, str] | None = None
    ) -> None:
        self.calls: list[str] = []
        self.refuse = refuse
        self.starts_after = starts_after
        self.health_checks = 0
        #: path suffix → the ``error`` its answer carries (a config that did not apply).
        self.errors = errors or {}

    def __call__(self, method: str, path: str, *, timeout: float = 120.0) -> dict[str, Any]:
        if path == "/health":
            self.health_checks += 1
            running = self.starts_after is not None and self.health_checks > self.starts_after
            return {"openclaw": {"status": "running" if running else "stopped"}}
        self.calls.append(path)
        if path == self.refuse:
            raise RuntimeError("ChatGPT is not signed in on this computer yet.")
        error = next((e for suffix, e in self.errors.items() if path.endswith(suffix)), None)
        if path.endswith("/prepare"):
            return {"ready": error is None, "error": error}
        return {"provider": "chatgpt", "offered": True, "applied": error is None, "error": error}


@pytest.fixture()
def console() -> Console:
    return Console(theme=cli._THEME, record=True, width=200)


def _wire(monkeypatch: pytest.MonkeyPatch, agent: _Agent, *, tty: bool = True, login: Any = 0) -> list[int]:
    monkeypatch.setattr(cli_subscription, "_call", agent)
    monkeypatch.setattr(cli_subscription, "_agent_reachable", lambda console: True)
    monkeypatch.setattr(cli_subscription, "_runs_on_host", lambda: False)
    monkeypatch.setattr(cli_subscription.sys.stdin, "isatty", lambda: tty)
    logins: list[int] = []

    def fake_login() -> int:
        logins.append(1)
        if isinstance(login, BaseException):
            raise login
        return int(login)

    monkeypatch.setattr(chatgpt_auth, "login_interactive", fake_login)
    return logins


def test_this_image_can_connect_both_plans(capsys: pytest.CaptureFixture[str], console: Console) -> None:
    assert cli_subscription.cmd_connect(console, ["--supported"]) == 0
    assert capsys.readouterr().out.strip() == "claude chatgpt"


def test_a_finished_sign_in_switches_the_agent(monkeypatch: pytest.MonkeyPatch, console: Console) -> None:
    agent = _Agent()
    logins = _wire(monkeypatch, agent)

    assert cli_subscription.cmd_connect(console, ["chatgpt"]) == 0
    assert agent.calls == ["/ai-subscription/chatgpt/prepare", "/ai-subscription/chatgpt/activate"]
    assert logins == [1]
    assert "Your agent now runs on your ChatGPT plan." in console.export_text()


@pytest.mark.parametrize(
    ("login", "exit_code", "message"),
    [
        pytest.param(1, 1, "The ChatGPT sign-in did not finish, so nothing changed.", id="sign-in-failed"),
        pytest.param(KeyboardInterrupt(), 130, "Sign-in cancelled. Your agent keeps running as it was.", id="ctrl-c"),
    ],
)
def test_an_unfinished_sign_in_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, console: Console, login: Any, exit_code: int, message: str
) -> None:
    agent = _Agent()
    _wire(monkeypatch, agent, login=login)

    assert cli_subscription.cmd_connect(console, ["chatgpt"]) == exit_code
    assert agent.calls == ["/ai-subscription/chatgpt/prepare", "/ai-subscription/chatgpt/cancel"]
    assert message in console.export_text()


def test_a_refused_switch_lets_the_sign_in_go(monkeypatch: pytest.MonkeyPatch, console: Console) -> None:
    agent = _Agent(refuse="/ai-subscription/chatgpt/activate")
    _wire(monkeypatch, agent)

    assert cli_subscription.cmd_connect(console, ["chatgpt"]) == 1
    assert agent.calls[-1] == "/ai-subscription/chatgpt/cancel"
    assert "ChatGPT is not signed in on this computer yet." in console.export_text()


def test_signing_in_needs_someone_at_the_terminal(monkeypatch: pytest.MonkeyPatch, console: Console) -> None:
    agent = _Agent()
    logins = _wire(monkeypatch, agent, tty=False)

    assert cli_subscription.cmd_connect(console, ["chatgpt"]) == 1
    assert (agent.calls, logins) == ([], [])
    assert "sellerclaw-agent connect chatgpt" in console.export_text()


class _Clock:
    """``time.monotonic`` / ``time.sleep`` for the wait: sleeping moves the clock, nothing blocks."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(cli_subscription.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(cli_subscription.time, "sleep", fake.sleep)
    return fake


def test_right_after_sign_in_it_waits_for_the_agent_to_start(
    monkeypatch: pytest.MonkeyPatch, console: Console, clock: _Clock
) -> None:
    """Fresh from SellerClaw sign-in, OpenClaw waits for the cloud's configuration; its sign-in needs it running."""
    agent = _Agent(starts_after=3)
    logins = _wire(monkeypatch, agent)

    assert cli_subscription.cmd_connect(console, ["chatgpt"]) == 0
    assert agent.health_checks == 4
    assert agent.calls == ["/ai-subscription/chatgpt/prepare", "/ai-subscription/chatgpt/activate"]
    assert logins == [1]


def test_an_agent_that_never_starts_is_not_signed_in(
    monkeypatch: pytest.MonkeyPatch, console: Console, clock: _Clock
) -> None:
    agent = _Agent(starts_after=None)
    logins = _wire(monkeypatch, agent)

    assert cli_subscription.cmd_connect(console, ["chatgpt"]) == 1
    assert clock.now >= cli_subscription.AGENT_START_WAIT_S
    assert (agent.calls, logins) == ([], [])
    text = console.export_text()
    assert "Your agent is not running, so ChatGPT cannot be connected yet." in text
    assert "press Start agent in the chat of the SellerClaw web panel" in text


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["chatgpt"], id="chatgpt"),
        pytest.param(["claude"], id="claude"),
        pytest.param(["claude", "--sign-in-again"], id="claude-sign-in-again"),
    ],
)
def test_a_source_checkout_signs_in_inside_the_agent_container(
    monkeypatch: pytest.MonkeyPatch, console: Console, argv: list[str]
) -> None:
    """``./setup.sh connect`` runs on the host, where neither OpenClaw nor Claude Code lives."""
    ran: list[list[str]] = []

    class _Done:
        returncode = 7

    def fake_run(command: list[str], *, cwd: Any, check: bool) -> _Done:
        ran.append(command)
        return _Done()

    monkeypatch.setattr(cli_subscription, "_agent_reachable", lambda console: True)
    monkeypatch.setattr(cli_subscription, "_runs_on_host", lambda: True)
    monkeypatch.setattr(cli, "_docker_compose_prefix", lambda: ["docker", "compose"])
    monkeypatch.setattr(cli, "_compose_env_file_args", lambda root: ["--env-file", ".env.local"])
    monkeypatch.setattr(cli_subscription.subprocess, "run", fake_run)

    assert cli_subscription.cmd_connect(console, argv) == 7
    assert ran == [
        [
            "docker", "compose", "--env-file", ".env.local", "exec", "-u", "node", "-e", "HOME=/home/node",
            "-w", "/app", "server", "python", "-m", "sellerclaw_agent", "connect", *argv,
        ]
    ]


def test_a_switch_that_did_not_apply_is_not_called_a_success(monkeypatch: pytest.MonkeyPatch, console: Console) -> None:
    """The sign-in is kept, but a failed restart leaves the agent on its old config — say so."""
    agent = _Agent(errors={"/activate": "supervisorctl: ERROR (spawn error)"})
    _wire(monkeypatch, agent)

    assert cli_subscription.cmd_connect(console, ["chatgpt"]) == 1
    text = " ".join(console.export_text().split())
    assert "Your ChatGPT sign-in is saved, but the agent could not switch to it: supervisorctl: ERROR (spawn error)" in text
    assert "Your agent now runs on your ChatGPT plan." not in text
    assert agent.calls == ["/ai-subscription/chatgpt/prepare", "/ai-subscription/chatgpt/activate"]


def test_a_config_that_did_not_apply_stops_before_the_sign_in(
    monkeypatch: pytest.MonkeyPatch, console: Console
) -> None:
    """Without ChatGPT's provider loaded OpenClaw would only refuse the sign-in with its own words."""
    agent = _Agent(errors={"/prepare": "bundle build failed"})
    logins = _wire(monkeypatch, agent)

    assert cli_subscription.cmd_connect(console, ["chatgpt"]) == 1
    assert logins == []
    assert agent.calls == ["/ai-subscription/chatgpt/prepare", "/ai-subscription/chatgpt/cancel"]
    assert "Could not get the agent ready: bundle build failed" in console.export_text()


@pytest.mark.parametrize(
    ("error", "exit_code", "message"),
    [
        pytest.param(None, 0, "Disconnected your ChatGPT plan. Your agent runs on SellerClaw credits again.", id="done"),
        pytest.param(
            "supervisorctl: ERROR",
            1,
            "Your plan is disconnected, but the agent could not rebuild its config: supervisorctl: ERROR",
            id="config-did-not-apply",
        ),
    ],
)
def test_disconnect_says_whether_the_agent_really_moved_back(
    monkeypatch: pytest.MonkeyPatch, console: Console, error: str | None, exit_code: int, message: str
) -> None:
    answers = {
        "/ai-subscription": {"provider": "chatgpt"},
        "/ai-subscription/disconnect": {"provider": None, "applied": error is None, "error": error},
    }
    monkeypatch.setattr(cli_subscription, "_call", lambda method, path, **_: answers[path])
    monkeypatch.setattr(cli_subscription, "_agent_reachable", lambda console: True)

    assert cli_subscription.cmd_disconnect(console) == exit_code
    assert message in " ".join(console.export_text().split())
