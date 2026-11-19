"""A scheduled task on a clean-disk (cloud) machine runs only when OpenClaw finds the LiteLLM key in
the environment: ``openclaw_start`` exports it from the config's litellm provider. These tests run
that shell function on its own, the way the start script calls it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[3] / "runtime" / "commands" / "openclaw_start"


def _function_source(name: str) -> str:
    """The body of one top-level function of the start script (it runs ``main`` when sourced)."""
    text = _SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, f"{name} is missing from {_SCRIPT}"
    return match.group(0)


def _run(config_path: Path) -> tuple[str, str]:
    """Run ``hydrate_litellm_inference_key`` → (exported LITELLM_API_KEY or "<unset>", log lines)."""
    script = "\n".join(
        [
            "set -euo pipefail",
            'log() { echo "[openclaw_start] $*" >&2; }',
            f"CONFIG_PATH={json.dumps(str(config_path))}",
            "unset LITELLM_API_KEY",
            _function_source("hydrate_litellm_inference_key"),
            "hydrate_litellm_inference_key",
            'echo "${LITELLM_API_KEY-<unset>}"',
        ]
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=True)
    return proc.stdout.strip(), proc.stderr


def _providers(**keys: str) -> dict[str, object]:
    return {"models": {"providers": {name: {"apiKey": key} for name, key in keys.items()}}}


@pytest.mark.parametrize(
    ("config", "expected_key"),
    [
        pytest.param(
            _providers(litellm="sk-agent-virtual-key", google="sk-google"),
            "sk-agent-virtual-key",
            id="litellm-provider-key-exported",
        ),
        pytest.param(_providers(google="sk-google"), "<unset>", id="no-litellm-provider"),
        pytest.param(_providers(litellm=""), "<unset>", id="empty-litellm-key"),
        pytest.param({"models": []}, "<unset>", id="malformed-config"),
    ],
)
def test_hydrate_litellm_inference_key(tmp_path: Path, config: object, expected_key: str) -> None:
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    key, log = _run(config_path)

    assert key == expected_key
    if expected_key == "<unset>":
        assert log == ""
    else:
        assert log.strip() == (
            "[openclaw_start] LITELLM_API_KEY hydrated from config litellm provider (scheduled-task inference key)"
        )
        assert expected_key not in log


def test_hydrate_litellm_inference_key_without_config(tmp_path: Path) -> None:
    assert _run(tmp_path / "missing.json") == ("<unset>", "")


def test_start_script_hydrates_the_key_before_the_gateway_starts() -> None:
    """The env must be set in ``main`` ahead of ``start_gateway``, which launches node with it."""
    main = _function_source("main")
    hydrate = main.find("hydrate_litellm_inference_key")
    assert hydrate != -1
    assert hydrate < main.find("start_gateway")
