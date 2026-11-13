"""The ``claude`` shim relays Claude Code untouched and notes its limits on the side."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from sellerclaw_agent.subscription import claude_wrapper

pytestmark = pytest.mark.unit

_WRAPPER = Path(claude_wrapper.__file__)

_STREAM = (
    '{"type":"system","subtype":"init"}\n'
    '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1794528000,'
    '"unifiedWindows":{"five_hour":{"utilization":1.0,"resetsAt":1794511200,"status":"rejected"}}}}\n'
    '{"type":"result","subtype":"success","result":"ok \\u00e9"}\n'
)


def _fake_claude(tmp_path: Path, *, exit_code: int) -> Path:
    """A stand-in binary: echoes its arguments to stderr, prints the stream, exits with ``exit_code``."""
    binary = tmp_path / "claude-real"
    stream = tmp_path / "stream.jsonl"
    stream.write_text(_STREAM, encoding="utf-8")
    binary.write_text(
        f"#!/bin/sh\necho \"args:$*\" >&2\ncat {stream}\nprintf 'no newline at end'\nexit {exit_code}\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


def _run(tmp_path: Path, binary: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = {
        **os.environ,
        "SELLERCLAW_CLAUDE_BINARY": str(binary),
        "SELLERCLAW_CLAUDE_LIMITS": str(tmp_path / "limits.json"),
    }
    return subprocess.run(  # noqa: S603 - the wrapper under test with fixed arguments
        [sys.executable, str(_WRAPPER), *args], env=env, capture_output=True, check=False, timeout=30
    )


def test_every_byte_passes_through_and_the_exit_code_is_kept(tmp_path: Path) -> None:
    result = _run(tmp_path, _fake_claude(tmp_path, exit_code=3), "-p", "--output-format", "stream-json")

    assert result.returncode == 3
    assert result.stdout == (_STREAM + "no newline at end").encode()
    assert result.stderr.decode().strip() == "args:-p --output-format stream-json"


def test_the_latest_rate_limit_event_lands_in_the_limits_file(tmp_path: Path) -> None:
    _run(tmp_path, _fake_claude(tmp_path, exit_code=0))

    limits = json.loads((tmp_path / "limits.json").read_text(encoding="utf-8"))
    assert limits["status"] == "rejected"
    assert limits["resets_at"] == "2026-11-13T00:00:00+00:00"
    assert limits["windows"]["five_hour"] == {
        "utilization": 1.0,
        "resets_at": "2026-11-12T19:20:00+00:00",
        "status": "rejected",
    }


def test_a_missing_install_says_how_to_set_it_up(tmp_path: Path) -> None:
    result = _run(tmp_path, tmp_path / "absent")

    assert result.returncode == 127
    assert b"sellerclaw-agent connect claude" in result.stderr
    assert not (tmp_path / "limits.json").exists()
