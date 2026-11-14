"""``sellerclaw-agent connect|disconnect``: run the agent on the owner's own AI plan, or on credits.

Runs inside the agent container (the installed ``sellerclaw-agent`` command wraps ``docker exec``)
as the ``node`` user that also runs OpenClaw. The owner's sign-in goes through the provider's own
page and program; this command never sees or stores it.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from sellerclaw_agent.http_clients import sync_client
from sellerclaw_agent.subscription import chatgpt_auth, claude_cli

#: What ``connect`` can do with this image (``install.sh`` asks before offering a choice).
SUPPORTED_PROVIDERS: tuple[str, ...] = ("claude", "chatgpt")
_NAMES = {"claude": "Claude", "chatgpt": "ChatGPT"}


def _base_and_headers() -> tuple[str, dict[str, str]]:
    from sellerclaw_agent.cli import _local_control_plane_auth_headers, agent_base_url

    base = agent_base_url()
    return base, _local_control_plane_auth_headers(base)


def _hint(command: str) -> str:
    """The command to run, phrased for the way this agent was installed."""
    from sellerclaw_agent.cli import _cli_hint

    return _cli_hint(command)


def _runs_on_host() -> bool:
    """A source checkout runs this CLI on the host (``./setup.sh``); the sign-in belongs in the container."""
    from sellerclaw_agent.cli import _installed_from_repo

    return _installed_from_repo()


def _data_dir() -> Path:
    from sellerclaw_agent.cli import _local_data_dir, agent_root

    return _local_data_dir(agent_root())


def _call(method: str, path: str, *, timeout: float = 120.0) -> dict[str, Any]:
    base, headers = _base_and_headers()
    with sync_client(timeout=timeout) as client:
        response = client.request(method, f"{base}{path}", headers=headers)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        message = detail.get("message") if isinstance(detail, dict) else None
        raise RuntimeError(message or f"{response.status_code} {response.text[:200]}")
    body = response.json()
    return body if isinstance(body, dict) else {}


def _agent_reachable(console: Console) -> bool:
    from sellerclaw_agent.cli import _cli_hint, agent_base_url, wait_for_agent

    base = agent_base_url()
    if wait_for_agent(base, console, timeout_s=15):
        return True
    console.print(
        f"[error]The agent is not reachable at {escape(base)}[/error]\n"
        f"[hint]Start it first: {_cli_hint('start')}[/hint]",
    )
    return False


def _connect_in_container(console: Console, provider: str, *, sign_in_again: bool = False) -> int:
    """Run ``connect`` where OpenClaw and Claude Code live: inside the agent's container."""
    from sellerclaw_agent.cli import _compose_env_file_args, _docker_compose_prefix, agent_root

    prefix = _docker_compose_prefix()
    if prefix is None:
        console.print("[error]Docker Compose v2 is required to reach the agent's container.[/error]")
        return 1
    root = agent_root()
    try:
        env_file_args = _compose_env_file_args(root)
    except RuntimeError as exc:
        console.print(f"[error]{escape(str(exc))}[/error]")
        return 1
    command = [
        *prefix,
        *env_file_args,
        "exec",
        "-u",
        "node",
        "-e",
        "HOME=/home/node",
        "-w",
        "/app",
        "server",
        "python",
        "-m",
        "sellerclaw_agent",
        "connect",
        provider,
        *([SIGN_IN_AGAIN_FLAG] if sign_in_again else []),
    ]
    return subprocess.run(command, cwd=root, check=False).returncode


#: How long ``connect chatgpt`` waits for OpenClaw to start: the ChatGPT sign-in goes through the
#: running gateway, and right after sign-in OpenClaw still waits for the cloud to configure and
#: start it. An agent its owner stopped never starts on its own, hence the hint while waiting.
AGENT_START_WAIT_S = 120.0


def _agent_runs() -> bool:
    try:
        health = _call("GET", "/health", timeout=5.0)
    except (RuntimeError, httpx.HTTPError, ValueError):
        return False
    openclaw = health.get("openclaw")
    return isinstance(openclaw, dict) and openclaw.get("status") == "running"


def _wait_until_agent_runs(console: Console) -> bool:
    if _agent_runs():
        return True
    deadline = time.monotonic() + AGENT_START_WAIT_S
    with console.status(
        "[info]Waiting for your agent to start…[/info] [hint](stopped? press Start agent in the chat of the "
        "SellerClaw web panel)[/hint]"
    ):
        while time.monotonic() < deadline:
            time.sleep(3.0)
            if _agent_runs():
                return True
    return False


def _connect_claude(console: Console, *, sign_in_again: bool = False) -> int:
    data_dir = _data_dir()
    console.print(
        Panel(
            "[label]Run your agent on your Claude plan[/label]\n"
            "[hint]Your agent's thinking will use your Claude Pro or Max plan instead of SellerClaw "
            "credits. Images, video, web search and memory still use credits.\n"
            "You sign in on Anthropic's own page — SellerClaw never sees your sign-in.\n"
            "When your plan's usage limit is used up, the agent waits until it resets.[/hint]",
            border_style="cyan",
            expand=False,
        ),
    )
    if not claude_cli.is_installed(data_dir):
        console.print("[info]Setting up Claude Code from Anthropic…[/info]")
        try:
            claude_cli.install(data_dir)
        except claude_cli.ClaudeInstallError as exc:
            console.print(f"[error]{escape(str(exc))}[/error]")
            return 1
        console.print("[success]  Done.[/success]")

    status = claude_cli.auth_status(data_dir)
    if sign_in_again or not status.on_subscription:
        if not sys.stdin.isatty():
            console.print(
                "[error]Signing in needs an interactive terminal.[/error]\n"
                "[hint]Run `sellerclaw-agent connect claude` directly in your terminal.[/hint]",
            )
            return 1
        console.print(
            "\n[info]Sign in to Claude[/info] [hint]— open the link below, sign in with your Claude "
            "account, then paste the code back here.[/hint]\n",
        )
        login_exit = claude_cli.login_interactive(data_dir)
        status = claude_cli.auth_status(data_dir)
        if not status.on_subscription:
            if sign_in_again and status.logged_in:
                # The new sign-in replaced the working one, with an account that has no Claude plan.
                console.print(
                    "[error]Claude is now signed in without a Claude plan, so your agent cannot think on it.[/error]\n"
                    "[hint]Sign in with the Claude account that has Pro or Max: "
                    f"{escape(_hint(f'connect claude {SIGN_IN_AGAIN_FLAG}'))}[/hint]",
                )
            else:
                console.print("[error]Claude is not signed in with a Claude plan, so nothing changed.[/error]")
            return 1
        if sign_in_again and login_exit != 0:
            console.print("[warning]The new Claude sign-in did not finish. Your agent keeps the one it had.[/warning]")
            return 1

    try:
        result = _call("POST", "/ai-subscription/claude/activate")
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[error]Could not switch the agent to your Claude plan: {escape(str(exc))}[/error]")
        return 1
    if _not_applied(console, result, "Claude"):
        return 1
    plan = f" ({status.subscription_type.capitalize()})" if status.subscription_type else ""
    console.print(f"\n[success]Your agent now runs on your Claude plan{plan}.[/success]")
    _warn_if_not_offered(console, result)
    return 0


def _restart_hint() -> str:
    """The installed command has ``restart``; a checkout's ``./setup.sh`` stops and starts."""
    return f"{_hint('stop')} && {_hint('start')}" if _runs_on_host() else _hint("restart")


def _not_applied(console: Console, result: dict[str, Any], name: str) -> bool:
    """The agent took the plan but could not rebuild its config on it (a failed restart, say).
    It keeps working as before — the previous plan is signed out only after a switch that took."""
    error = result.get("error")
    if not error:
        return False
    console.print(
        f"[error]Your {name} sign-in is saved, but the agent could not switch to it: {escape(str(error))}[/error]\n"
        f"[hint]Your agent keeps running as it was. Restart it and try again: {escape(_restart_hint())}, "
        f"then {escape(_hint('connect'))}[/hint]",
    )
    return True


def _warn_if_not_offered(console: Console, result: dict[str, Any]) -> None:
    """``offered`` is ``None`` until the cloud sends its configuration — not a refusal."""
    if result.get("offered") is False:
        console.print(
            "[warning]SellerClaw has not offered your plan to this agent yet.[/warning]\n"
            "[hint]Your agent runs in SellerClaw's cloud, where it keeps using credits. To use your plan, "
            'open Hosting in SellerClaw and press "Move to your computer".[/hint]',
        )


def _cancel_chatgpt_sign_in() -> None:
    try:
        _call("POST", "/ai-subscription/chatgpt/cancel")
    except (RuntimeError, httpx.HTTPError):
        pass  # it expires on its own


def _connect_chatgpt(console: Console) -> int:
    console.print(
        Panel(
            "[label]Run your agent on your ChatGPT plan[/label]\n"
            "[hint]Your agent's thinking will use your ChatGPT Plus or Pro plan instead of SellerClaw "
            "credits. Images, video, web search and memory still use credits.\n"
            "You sign in on OpenAI's own page — SellerClaw never sees your sign-in.\n"
            "When your plan's usage limit is used up, the agent waits until it resets.[/hint]",
            border_style="cyan",
            expand=False,
        ),
    )
    if not sys.stdin.isatty():
        console.print(
            "[error]Signing in needs an interactive terminal.[/error]\n"
            "[hint]Run `sellerclaw-agent connect chatgpt` directly in your terminal.[/hint]",
        )
        return 1
    if not _wait_until_agent_runs(console):
        console.print(
            "[error]Your agent is not running, so ChatGPT cannot be connected yet.[/error]\n"
            "[hint]If it is stopped, press Start agent in the chat of the SellerClaw web panel. "
            f"Then run: {escape(_hint('connect chatgpt'))}[/hint]",
        )
        return 1
    console.print("[info]Getting your agent ready for the ChatGPT sign-in…[/info]")
    try:
        prepared = _call("POST", "/ai-subscription/chatgpt/prepare", timeout=180.0)
    except (RuntimeError, httpx.HTTPError) as exc:
        _cancel_chatgpt_sign_in()
        console.print(f"[error]Could not get the agent ready: {escape(str(exc))}[/error]")
        return 1
    if prepared.get("error"):
        # The config with ChatGPT's provider did not apply: OpenClaw would only refuse the sign-in.
        _cancel_chatgpt_sign_in()
        console.print(f"[error]Could not get the agent ready: {escape(str(prepared['error']))}[/error]")
        return 1
    if not prepared.get("ready"):
        # The sign-in goes through the gateway, which offers it only once ChatGPT's provider has
        # loaded; trying anyway would only end in OpenClaw's own "No Gateway-compatible login".
        _cancel_chatgpt_sign_in()
        console.print(
            "[error]Your agent did not load ChatGPT support in time, so nothing changed.[/error]\n"
            f"[hint]Try again in a minute: {escape(_hint('connect chatgpt'))}[/hint]",
        )
        return 1
    console.print(
        "\n[info]Sign in to ChatGPT[/info] [hint]— a link and a code appear below. Open the link, sign "
        "in with the account that has ChatGPT Plus or Pro, enter the code, then come back here.[/hint]\n",
    )
    try:
        exit_code = chatgpt_auth.login_interactive()
    except KeyboardInterrupt:
        _cancel_chatgpt_sign_in()
        console.print("\n[warning]Sign-in cancelled. Your agent keeps running as it was.[/warning]")
        return 130
    if exit_code != 0:
        _cancel_chatgpt_sign_in()
        console.print("[error]The ChatGPT sign-in did not finish, so nothing changed.[/error]")
        return 1
    console.print("[info]Switching your agent to your ChatGPT plan — it restarts, about a minute…[/info]")
    try:
        result = _call("POST", "/ai-subscription/chatgpt/activate", timeout=300.0)
    except (RuntimeError, httpx.HTTPError) as exc:
        _cancel_chatgpt_sign_in()
        console.print(f"[error]Could not switch the agent to your ChatGPT plan: {escape(str(exc))}[/error]")
        return 1
    if _not_applied(console, result, "ChatGPT"):
        return 1
    console.print("\n[success]Your agent now runs on your ChatGPT plan.[/success]")
    _warn_if_not_offered(console, result)
    return 0


#: ``connect <plan> --sign-in-again``: a new sign-in even while one works (another account, say).
#: ChatGPT always signs in anew; Claude otherwise keeps a working sign-in.
SIGN_IN_AGAIN_FLAG = "--sign-in-again"


def cmd_connect(console: Console, argv: list[str]) -> int:
    """``connect <claude|chatgpt> [--sign-in-again]`` / ``connect`` (asks) / ``connect --supported``."""
    if "--supported" in argv:
        print(" ".join(SUPPORTED_PROVIDERS))  # noqa: T201 - machine-readable for install.sh
        return 0
    names = [arg.strip().lower() for arg in argv if not arg.startswith("-")]
    sign_in_again = SIGN_IN_AGAIN_FLAG in argv
    if not names and sys.stdin.isatty():
        return choose_ai_source(console)
    provider = names[0] if names else ""
    if provider not in SUPPORTED_PROVIDERS:
        offered = " | ".join(SUPPORTED_PROVIDERS)
        console.print(f"[error]Choose what to connect: sellerclaw-agent connect {offered}[/error]")
        return 2
    if not _agent_reachable(console):
        return 1
    if _runs_on_host():
        return _connect_in_container(console, provider, sign_in_again=sign_in_again)
    if provider == "chatgpt":
        return _connect_chatgpt(console)
    return _connect_claude(console, sign_in_again=sign_in_again)


def cmd_disconnect(console: Console) -> int:
    if not _agent_reachable(console):
        return 1
    try:
        before = _call("GET", "/ai-subscription")
        after = _call("POST", "/ai-subscription/disconnect")
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[error]Could not disconnect: {escape(str(exc))}[/error]")
        return 1
    if after.get("error"):
        console.print(
            f"[error]Your plan is disconnected, but the agent could not rebuild its config: "
            f"{escape(str(after['error']))}[/error]\n"
            f"[hint]Restart it to finish: {escape(_restart_hint())}[/hint]",
        )
        return 1
    provider = before.get("provider")
    if provider:
        name = _NAMES.get(str(provider), str(provider))
        console.print(f"[success]Disconnected your {name} plan. Your agent runs on SellerClaw credits again.[/success]")
    else:
        console.print("[info]Your agent already runs on SellerClaw credits.[/info]")
    return 0


def _when(raw: object, now: datetime | None = None) -> str:
    """A reset on the machine's clock; the date too once a weekday alone could mean today."""
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw).astimezone()
    except ValueError:
        return ""
    if moment - (now or datetime.now(UTC)) >= timedelta(days=6):
        return moment.strftime("%a %d %b %H:%M")
    return moment.strftime("%a %H:%M")


def ai_state() -> dict[str, Any] | None:
    """What powers the agent, as the agent reports it; None when it cannot say (stopped, too old)."""
    try:
        return _call("GET", "/ai-subscription", timeout=5.0)
    except (RuntimeError, httpx.HTTPError, ValueError):  # ValueError: an answer that is not JSON
        return None


def _status_of(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get("status")
    return raw if isinstance(raw, dict) else {}


def _plan_title(provider: str, status: dict[str, Any]) -> str:
    """"your Claude plan (Max)" — raw text; markup callers escape it."""
    plan = status.get("plan")
    name = _NAMES.get(provider, provider)
    return f"your {name} plan" + (f" ({str(plan).capitalize()})" if plan else "")


_WINDOW_LABELS = {"5h": "5-hour", "week": "weekly"}


def ai_status_lines() -> list[str]:
    """The "AI:" block of ``sellerclaw-agent status``; empty when the agent cannot say."""
    state = ai_state()
    if state is None:
        return []
    provider = state.get("provider")
    if not provider:
        hint = escape(_hint("connect claude | chatgpt"))
        return [f"AI: SellerClaw credits  [hint]({hint} — use your own plan)[/hint]"]
    provider = str(provider)
    status = _status_of(state)
    lines = [f"AI: {escape(_plan_title(provider, status))}"]
    if state.get("offered") is False:
        lines.append("    [warning]not in use — your agent runs in the cloud: move it to your computer in Hosting[/warning]")
    if status.get("state") == "limit_reached":
        until = _when(status.get("resumes_at"))
        lines.append(f"    [warning]usage limit used up — waiting until {until or 'it resets'}[/warning]")
    elif status.get("state") == "signed_out":
        lines.append(f"    [warning]signed out — run: {escape(_hint(f'connect {provider}'))}[/warning]")
    for window in status.get("windows") or []:
        if not isinstance(window, dict):
            continue
        label = _WINDOW_LABELS.get(str(window.get("window")), str(window.get("window")))
        reset = _when(window.get("reset_at"))
        lines.append(
            f"    {label} limit: {window.get('used_percent')}% used" + (f", resets {reset}" if reset else "")
        )
    return lines


def ai_panel_row(state: dict[str, Any] | None) -> tuple[str, str] | None:
    """The "AI" row of the live status panel as (text, style); None — no row — when the agent cannot say."""
    if state is None:
        return None
    provider = state.get("provider")
    if not provider:
        return f"SellerClaw credits (to use your ChatGPT or Claude plan: {_hint('connect chatgpt | claude')})", ""
    provider = str(provider)
    status = _status_of(state)
    title = _plan_title(provider, status)
    if state.get("offered") is False:
        return f"{title} — not in use: your agent runs in the cloud; move it to your computer in Hosting", "yellow"
    if status.get("state") == "limit_reached":
        until = _when(status.get("resumes_at"))
        return f"{title} — usage limit used up, waiting until {until or 'it resets'}", "yellow"
    if status.get("state") == "signed_out":
        return f"{title} — signed out, run: {_hint(f'connect {provider}')}", "bold red"
    used = [
        f"{_WINDOW_LABELS.get(str(window.get('window')), str(window.get('window')))} {window.get('used_percent')}%"
        for window in status.get("windows") or []
        if isinstance(window, dict) and window.get("used_percent") is not None
    ]
    return title + (f", limits used: {', '.join(used)}" if used else ""), "green"


#: What the owner reads before choosing; the installer (``install.sh``) says the same.
OWN_PLAN_PITCH = (
    "[label]Already pay for ChatGPT or Claude?[/label]\n"
    "[hint]Your agent can think on that plan instead of SellerClaw credits: its chats and tasks spend no\n"
    "credits, and every effort level is yours, Max included. Images, video, web search and memory\n"
    "still use credits. You sign in on OpenAI's or Anthropic's own page — SellerClaw never sees it.[/hint]"
)


def _other_plan(provider: str) -> str | None:
    return next((p for p in SUPPORTED_PROVIDERS if p != provider), None)


def ai_source_choices(state: dict[str, Any]) -> tuple[list[tuple[str, str]], str]:
    """The answers to "What should power your agent?" for where the agent stands now, and the default.

    On credits: credits or a plan. On a plan: keep it, the other plan, a new sign-in (another
    account, or one that ended — then it comes first), or back to credits.
    """
    provider = state.get("provider")
    if not provider:
        return [
            ("credits", "SellerClaw credits"),
            ("chatgpt", "My ChatGPT plan (Plus or Pro)"),
            ("claude", "My Claude plan (Pro or Max)"),
        ], "credits"
    provider = str(provider)
    name = _NAMES.get(provider, provider)
    other = _other_plan(provider)
    switch = [(other, f"Switch to my {_NAMES[other]} plan ({_TIERS[other]})")] if other else []
    again = ("again", f"Sign in to {name} again")
    credits = ("credits", "Switch to SellerClaw credits")
    if _status_of(state).get("state") == "signed_out":
        return [again, *switch, credits, ("keep", "Not now")], "again"
    return [("keep", f"Keep my {name} plan"), *switch, again, credits], "keep"


_TIERS = {"claude": "Pro or Max", "chatgpt": "Plus or Pro"}


def _where_it_stands(state: dict[str, Any]) -> str:
    """One line on what powers the agent now, ahead of the question (markup)."""
    provider = str(state["provider"])
    status = _status_of(state)
    title = escape(_plan_title(provider, status))
    name = _NAMES.get(provider, provider)
    if status.get("state") == "signed_out":
        return f"[warning]Your agent runs on {title}, but the {name} sign-in has ended — it is waiting.[/warning]"
    if status.get("state") == "limit_reached":
        until = _when(status.get("resumes_at"))
        return (
            f"Your agent runs on [label]{title}[/label]. "
            f"[warning]Its usage limit is used up until {until or 'it resets'}.[/warning]"
        )
    if state.get("offered") is False:
        return (
            f"Your agent is connected to {title}, [warning]but it is not in use: your agent runs in the "
            "cloud. Move it to your computer in Hosting.[/warning]"
        )
    return f"Your agent runs on [label]{title}[/label]."


def choose_ai_source(console: Console) -> int:
    """"What should power your agent?" — asked by ``./setup.sh`` after sign-in, by the installer,
    and by ``connect`` with no plan named.

    Only with someone at the terminal to sign in on the provider's page. On credits it makes the
    case for the owner's own plan; on a plan it offers to keep it, switch, sign in again or go
    back to credits. Returns the exit code of what it did (0 when nothing changed).
    """
    from sellerclaw_agent.cli import _select_option

    if not sys.stdin.isatty():
        return 0
    state = ai_state()
    if state is None:
        return 0
    provider = state.get("provider")
    console.print()
    if provider:
        console.print(_where_it_stands(state))
    else:
        console.print(Panel(OWN_PLAN_PITCH, border_style="cyan", expand=False, padding=(1, 3)))
    options, default = ai_source_choices(state)
    choice = _select_option(console, "What should power your agent?", options, default_value=default)

    if choice == "keep" or (choice == "credits" and not provider):
        if not provider:
            console.print(
                f"[hint]Your agent runs on SellerClaw credits. Switch any time: "
                f"{escape(_hint('connect'))}[/hint]",
            )
        console.print()
        return 0
    if choice == "credits":
        code = cmd_disconnect(console)
        console.print()
        return code

    target = str(provider) if choice == "again" else choice
    argv = [target, SIGN_IN_AGAIN_FLAG] if choice == "again" else [target]
    code = cmd_connect(console, argv)
    if code != 0:
        name = _NAMES.get(target, target)
        if choice == "again":
            what = f"{name} was not signed in again."
        elif not provider:
            what = f"Your {name} plan was not connected. The agent runs on SellerClaw credits."
        else:
            what = (
                f"Your {name} plan was not connected. "
                f"Your agent keeps your {_NAMES.get(str(provider), str(provider))} plan."
            )
        console.print(f"[warning]{what} Try again any time: {escape(_hint('connect'))}[/warning]")
    console.print()
    return code
