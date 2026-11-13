"""The owner's own subscription in the generated OpenClaw config (``llm.subscription`` + the choice).

Claude runs ``anthropic/*`` on the Claude Code CLI; ChatGPT ``openai/*`` on OpenClaw's own loop.
Every text role moves to the plan, nothing falls back to our paid models, and media/PDF stay put.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sellerclaw_agent.bundle.builder import BundleBuilder
from sellerclaw_agent.bundle.manifest import bundle_manifest_from_mapping
from sellerclaw_agent.subscription.chatgpt_auth import PENDING_TTL, mark_login_pending
from sellerclaw_agent.subscription.overlay import resolve_overlay
from sellerclaw_agent.subscription.state import clear_choice, load_choice, save_choice, status_path
from sellerclaw_agent.test_manifest_fixtures import load_manifest_v2_mapping

pytestmark = pytest.mark.unit

_CLOUD = "https://example.ngrok-free.dev"

_LINEUPS: dict[str, Any] = {
    "chatgpt": {
        "supervisor": {"models": ["gpt-6.1-sol", "gpt-6-sol"], "reasoning": True, "thinking": "high"},
        "specialist": {"models": ["gpt-6.1-terra", "gpt-6-sol"], "reasoning": True},
        "utility": {"models": ["gpt-6-luna"], "reasoning": False},
    },
    "claude": {
        "supervisor": {"models": ["claude-opus-5-5", "claude-sonnet-5-5"], "reasoning": True, "thinking": "max"},
        "specialist": {"models": ["claude-sonnet-5-5"], "reasoning": True},
        "utility": {"models": ["claude-haiku-5-5", "claude-haiku-4-5"], "reasoning": False},
    },
}


def _mapping(*, lineups: dict[str, Any] | None = _LINEUPS) -> dict[str, Any]:
    mapping = copy.deepcopy(load_manifest_v2_mapping())
    if lineups is not None:
        mapping["llm"]["subscription"] = copy.deepcopy(lineups)
    return mapping


def _config(data_dir: Path, mapping: dict[str, Any]) -> dict[str, Any]:
    manifest = bundle_manifest_from_mapping(mapping)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SELLERCLAW_API_URL", _CLOUD)
        result = BundleBuilder().build(
            manifest, gateway_token="gw", hooks_token="hooks", agent_api_key="k", data_dir=data_dir
        )
    return json.loads(result.openclaw_config)


def test_manifest_round_trips_the_lineups_and_skips_unknown_providers() -> None:
    mapping = _mapping(lineups={**_LINEUPS, "gemini": {"supervisor": {"models": ["x"]}}})

    manifest = bundle_manifest_from_mapping(mapping)

    assert set(manifest.llm.subscription) == {"chatgpt", "claude"}
    claude = manifest.llm.subscription["claude"]
    assert claude.supervisor.models == ("claude-opus-5-5", "claude-sonnet-5-5")
    assert claude.supervisor.thinking == "max"
    assert claude.utility.reasoning is False
    llm = cast(dict[str, Any], manifest.to_save_manifest_mapping()["llm"])
    assert llm["subscription"] == _LINEUPS


@pytest.mark.parametrize(
    ("block", "message"),
    [
        pytest.param({"claude": {"supervisor": {"models": []}}}, "supervisor.models must list", id="no-models"),
        pytest.param({"claude": "opus"}, "llm.subscription.claude", id="not-a-mapping"),
    ],
)
def test_a_malformed_lineup_for_a_known_provider_is_refused(block: dict[str, Any], message: str) -> None:
    with pytest.raises((ValueError, TypeError), match=message):
        bundle_manifest_from_mapping(_mapping(lineups=block))


def test_credits_mode_is_unchanged_when_nothing_is_connected(tmp_path: Path) -> None:
    cfg = _config(tmp_path, _mapping())

    defaults = cfg["agents"]["defaults"]
    assert defaults["model"]["primary"].startswith("litellm/")
    assert "models" not in defaults
    assert "utilityModel" not in defaults
    assert "anthropic" in cfg["models"]["providers"]
    assert "aiSubscription" not in cfg["plugins"]["entries"]["sellerclaw-ui"]["config"]


def test_claude_moves_every_text_role_onto_the_claude_code_cli(tmp_path: Path) -> None:
    save_choice(tmp_path, "claude")

    cfg = _config(tmp_path, _mapping())

    defaults = cfg["agents"]["defaults"]
    assert defaults["model"] == {"primary": "anthropic/claude-opus-5-5", "fallbacks": ["anthropic/claude-sonnet-5-5"]}
    assert defaults["utilityModel"] == "anthropic/claude-haiku-5-5"
    assert defaults["compaction"]["model"] == "anthropic/claude-haiku-5-5"
    assert defaults["compaction"]["memoryFlush"]["model"] == "anthropic/claude-haiku-5-5"
    assert defaults["heartbeat"]["model"] == "anthropic/claude-haiku-5-5"
    # The runtime is pinned per model — never provider-wide — and covers every candidate.
    assert defaults["models"] == {
        ref: {"agentRuntime": {"id": "claude-cli"}}
        for ref in (
            "anthropic/claude-opus-5-5",
            "anthropic/claude-sonnet-5-5",
            "anthropic/claude-haiku-5-5",
            "anthropic/claude-haiku-4-5",
        )
    }
    entries = cfg["agents"]["entries"]
    assert entries["supervisor"]["model"] == {
        "primary": "anthropic/claude-opus-5-5",
        "fallbacks": ["anthropic/claude-sonnet-5-5"],
    }
    assert entries["supervisor"]["thinkingDefault"] == "max"
    assert entries["supplier"]["model"] == "anthropic/claude-sonnet-5-5"  # a "secondary" specialist
    assert entries["scout"]["model"]["primary"] == "anthropic/claude-opus-5-5"  # a "primary" specialist
    # Nothing on the plan falls back to a paid model.
    for entry in entries.values():
        model = entry["model"]
        refs = [model] if isinstance(model, str) else [model["primary"], *model["fallbacks"]]
        assert all(ref.startswith("anthropic/") for ref in refs)


def test_claude_mode_moves_the_pdf_passthrough_out_of_the_subscriptions_way(tmp_path: Path) -> None:
    save_choice(tmp_path, "claude")

    cfg = _config(tmp_path, _mapping())

    providers = cfg["models"]["providers"]
    assert "anthropic" not in providers
    assert providers["sellerclaw-anthropic"]["baseUrl"].endswith("/litellm/anthropic")
    pdf = cfg["agents"]["defaults"]["pdfModel"]
    assert pdf["primary"] == "sellerclaw-anthropic/claude-sonnet-4-6"
    assert pdf["fallbacks"] == ["google/gemini-3.1-pro-preview"]
    assert pdf["primary"] not in cfg["agents"]["defaults"]["models"]


def test_claude_mode_turns_on_its_plugin_and_tells_ours_where_the_status_is(tmp_path: Path) -> None:
    save_choice(tmp_path, "claude")

    cfg = _config(tmp_path, _mapping())

    plugins = cfg["plugins"]
    assert "anthropic" in plugins["allow"]
    assert plugins["entries"]["anthropic"] == {"enabled": True}
    expected = {"provider": "claude", "statusPath": str(status_path(tmp_path))}
    assert plugins["entries"]["sellerclaw-ui"]["config"]["aiSubscription"] == expected
    assert cfg["channels"]["sellerclaw-ui"]["aiSubscription"] == expected


def test_chatgpt_runs_on_openclaws_own_loop(tmp_path: Path) -> None:
    save_choice(tmp_path, "chatgpt")

    cfg = _config(tmp_path, _mapping())

    defaults = cfg["agents"]["defaults"]
    assert defaults["model"] == {"primary": "openai/gpt-6.1-sol", "fallbacks": ["openai/gpt-6-sol"]}
    assert defaults["utilityModel"] == "openai/gpt-6-luna"
    assert defaults["models"] == {
        ref: {"agentRuntime": {"id": "openclaw"}}
        for ref in ("openai/gpt-6.1-sol", "openai/gpt-6-sol", "openai/gpt-6.1-terra", "openai/gpt-6-luna")
    }
    assert "openai" in cfg["plugins"]["allow"]
    # The Anthropic passthrough is only renamed for Claude.
    assert "anthropic" in cfg["models"]["providers"]
    assert "openai" not in cfg["models"]["providers"]


def test_a_connected_machine_without_a_lineup_stays_on_credits(tmp_path: Path) -> None:
    """No lineup from the cloud is how the cloud switches the feature off for an agent."""
    save_choice(tmp_path, "claude")

    cfg = _config(tmp_path, _mapping(lineups=None))

    assert cfg["agents"]["defaults"]["model"]["primary"].startswith("litellm/")
    assert "models" not in cfg["agents"]["defaults"]


def test_the_choice_round_trips_and_disconnect_forgets_it(tmp_path: Path) -> None:
    assert load_choice(tmp_path) is None
    saved = save_choice(tmp_path, "claude")

    loaded = load_choice(tmp_path)
    assert loaded == saved
    status_path(tmp_path).write_text("{}", encoding="utf-8")

    clear_choice(tmp_path)

    assert load_choice(tmp_path) is None
    assert not status_path(tmp_path).exists()


def test_an_unknown_provider_cannot_be_chosen(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown subscription 'gemini'"):
        save_choice(tmp_path, "gemini")


def test_overlay_is_none_without_a_choice() -> None:
    manifest = bundle_manifest_from_mapping(_mapping())

    assert resolve_overlay(manifest.llm.subscription, None) is None


def test_a_chatgpt_sign_in_under_way_loads_its_provider_and_nothing_else(tmp_path: Path) -> None:
    """OpenClaw's online login offers only what the running gateway loaded; the models stay on credits."""
    mark_login_pending(tmp_path)

    cfg = _config(tmp_path, _mapping())

    assert "openai" in cfg["plugins"]["allow"]
    assert cfg["plugins"]["entries"]["openai"] == {"enabled": True}
    assert cfg["agents"]["defaults"]["model"]["primary"].startswith("litellm/")
    assert "models" not in cfg["agents"]["defaults"]
    assert "aiSubscription" not in cfg["plugins"]["entries"]["sellerclaw-ui"]["config"]


def test_switching_from_claude_keeps_claude_running_while_chatgpt_signs_in(tmp_path: Path) -> None:
    save_choice(tmp_path, "claude")
    mark_login_pending(tmp_path)

    cfg = _config(tmp_path, _mapping())

    assert {"anthropic", "openai"} <= set(cfg["plugins"]["allow"])
    assert cfg["plugins"]["entries"]["openai"] == {"enabled": True}
    assert cfg["agents"]["defaults"]["model"]["primary"] == "anthropic/claude-opus-5-5"


@pytest.mark.parametrize(
    "abandoned",
    [pytest.param(False, id="credits"), pytest.param(True, id="abandoned-sign-in")],
)
def test_no_chatgpt_provider_without_a_sign_in_under_way(tmp_path: Path, abandoned: bool) -> None:
    if abandoned:
        mark_login_pending(tmp_path, now=datetime.now(tz=UTC) - PENDING_TTL)

    cfg = _config(tmp_path, _mapping())

    assert "openai" not in cfg["plugins"]["allow"]
    assert "openai" not in cfg["plugins"]["entries"]
