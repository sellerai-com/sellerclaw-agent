"""The image, the code and OpenClaw agree on where Claude Code keeps its sign-in and transcripts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sellerclaw_agent.subscription import chatgpt_auth, claude_cli
from sellerclaw_agent.subscription.overlay import _ROUTES

pytestmark = pytest.mark.unit

_DOCKERFILE = Path(__file__).resolve().parents[3] / "runtime" / "Dockerfile"


def test_claude_code_config_dir_is_on_the_volume_and_where_openclaw_reads_transcripts() -> None:
    """``CLAUDE_CONFIG_DIR``, the ``~/.claude`` link and the code name one directory.

    OpenClaw reads Claude Code's transcripts from ``$HOME/.claude/projects`` only; if the link and
    the config dir part ways, a specialist's result never reaches the main agent again.
    """
    text = _DOCKERFILE.read_text(encoding="utf-8")
    config_dir = str(claude_cli.claude_config_dir(Path("/data")))

    env = re.search(r"^ENV CLAUDE_CONFIG_DIR=(\S+)$", text, re.MULTILINE)
    link = re.search(r"ln -s (\S+) /home/node/\.claude\b", text)

    assert env is not None and env.group(1) == config_dir
    assert link is not None and link.group(1) == config_dir


def _kept_extensions() -> set[str]:
    match = re.search(r'keep="([^"]+)"', _DOCKERFILE.read_text(encoding="utf-8"))
    assert match is not None, "the bundled-extension keep list moved"
    return set(match.group(1).split())


def test_every_plugin_a_plan_switches_on_survives_the_image_prune() -> None:
    """The image deletes every bundled extension not on its keep list; allowing a deleted plugin in
    the config loads nothing, and OpenClaw then refuses the plan's sign-in ("No Gateway-compatible
    login matches this provider") — what the first live ChatGPT run hit."""
    needed = {route.plugin_id for route in _ROUTES.values()} | {chatgpt_auth.OPENAI_PROVIDER}

    assert needed <= _kept_extensions()
