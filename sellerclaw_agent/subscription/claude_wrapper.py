"""``claude`` as OpenClaw finds it on PATH: Anthropic's unmodified binary, with its limits noted.

OpenClaw drives Claude Code over stdio (stream-json). Claude Code reports the owner's plan limits in
that stream itself — ``rate_limit_event`` lines carrying the 5-hour and weekly windows, how full
they are and when they reset — but OpenClaw does not surface them. This shim runs the real binary
untouched, relays every byte between it and OpenClaw, and copies the latest of those events to a
small file the agent server reads (the panel's limit bars, the exact time a waiting agent resumes).

It never alters what passes through, never reads Claude Code's sign-in, and adds nothing to the
command line. Standalone on purpose (stdlib only, no package imports): it starts with every
Claude Code process.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

_DATA_DIR = os.environ.get("SELLERCLAW_DATA_DIR") or "/data"
REAL_BINARY = os.environ.get("SELLERCLAW_CLAUDE_BINARY") or os.path.join(
    _DATA_DIR, "ai-subscription", "claude-home", ".local", "bin", "claude"
)
LIMITS_PATH = os.environ.get("SELLERCLAW_CLAUDE_LIMITS") or os.path.join(
    _DATA_DIR, "ai-subscription", "claude-limits.json"
)
_MARKER = b'"rate_limit_event"'
_FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)


def _iso(epoch: object) -> str | None:
    if isinstance(epoch, (int, float)) and epoch > 0:
        return datetime.fromtimestamp(float(epoch), tz=UTC).isoformat()
    return None


def limits_from_event(event: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    """The part of a ``rate_limit_event`` worth keeping, or ``None`` when it is not one."""
    if event.get("type") != "rate_limit_event":
        return None
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    windows: dict[str, Any] = {}
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        for name, window in unified.items():
            if not isinstance(window, dict):
                continue
            utilization = window.get("utilization")
            windows[str(name)] = {
                "utilization": float(utilization) if isinstance(utilization, (int, float)) else None,
                "resets_at": _iso(window.get("resetsAt")),
                "status": window.get("status") if isinstance(window.get("status"), str) else None,
            }
    return {
        "observed_at": now.isoformat(),
        "status": info.get("status") if isinstance(info.get("status"), str) else None,
        "rate_limit_type": info.get("rateLimitType") if isinstance(info.get("rateLimitType"), str) else None,
        "resets_at": _iso(info.get("resetsAt")),
        "windows": windows,
    }


def _write_limits(limits: dict[str, Any]) -> None:
    tmp = f"{LIMITS_PATH}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(LIMITS_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(limits, fh)
        os.replace(tmp, LIMITS_PATH)
    except OSError:
        # Noting limits is a courtesy; it must never break the conversation it rides on.
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _note(line: bytes) -> None:
    if _MARKER not in line:
        return
    try:
        event = json.loads(line)
    except ValueError:
        return
    if isinstance(event, dict):
        limits = limits_from_event(event, now=datetime.now(tz=UTC))
        if limits is not None:
            _write_limits(limits)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def main(argv: list[str]) -> int:
    if not os.path.exists(REAL_BINARY):
        sys.stderr.write(
            "Claude Code is not installed on this computer. "
            "Run `sellerclaw-agent connect claude` to set it up.\n"
        )
        return 127
    child = subprocess.Popen([REAL_BINARY, *argv], stdout=subprocess.PIPE)  # noqa: S603 - fixed binary
    for sig in _FORWARDED_SIGNALS:
        signal.signal(sig, lambda signum, _frame: child.send_signal(signum))
    assert child.stdout is not None
    out_fd = sys.stdout.fileno()
    pending = b""
    try:
        while True:
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk:
                break
            _write_all(out_fd, chunk)
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                _note(line)
    except BrokenPipeError:
        # OpenClaw went away: the conversation is over, so is the process serving it.
        child.terminate()
    if pending:
        _note(pending)
    code = child.wait()
    if code < 0:
        return 128 - code
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
