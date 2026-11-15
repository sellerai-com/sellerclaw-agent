"""Installing Claude Code on demand: the pinned build alone, resumed after a dropped line, checked.

Anthropic's installer script downloaded the latest build and then the pinned one (~660 MB) with its
own short timeouts; on a slow line (staging, 14.11.2026) it could never finish.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sellerclaw_agent.subscription import claude_cli

pytestmark = pytest.mark.unit

_BUILD = b"\x7fELF" + bytes(range(256)) * 40  # stands in for the ~290 MB binary
_VERSION = claude_cli.CLAUDE_CODE_VERSION
_BINARY_URL = f"{claude_cli.CLAUDE_RELEASES_URL}/{_VERSION}/linux-x64/claude"
_MANIFEST_URL = f"{claude_cli.CLAUDE_RELEASES_URL}/{_VERSION}/manifest.json"


def _manifest(*, checksum: str | None = None, platform: str = "linux-x64") -> dict[str, Any]:
    return {
        "version": _VERSION,
        "platforms": {
            platform: {"binary": "claude", "checksum": checksum or hashlib.sha256(_BUILD).hexdigest(), "size": len(_BUILD)}
        },
    }


class _ReleaseBucket:
    """downloads.claude.ai as ``install`` sees it. ``binary`` answers in order (the last repeats):
    ``"full"``, ``"half"`` (the line drops mid-file), ``"rest"`` (honours Range), ``"down"``."""

    def __init__(self, *, manifest: dict[str, Any], binary: list[str]) -> None:
        self.manifest = manifest
        self.binary = binary
        self.ranges: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == _MANIFEST_URL:
            return httpx.Response(200, json=self.manifest)
        assert str(request.url) == _BINARY_URL
        self.ranges.append(request.headers.get("range"))
        answer = self.binary[min(len(self.ranges), len(self.binary)) - 1]
        if answer == "down":
            raise httpx.ConnectTimeout("timed out", request=request)
        if answer == "half":
            return httpx.Response(200, content=_BUILD[: len(_BUILD) // 2])
        if answer == "rest":
            start = int(request.headers["range"].removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=_BUILD[start:])
        return httpx.Response(200, content=_BUILD)


@pytest.fixture
def release_bucket(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _ReleaseBucket]:
    real_client = httpx.Client

    def _install(*, binary: list[str], manifest: dict[str, Any] | None = None) -> _ReleaseBucket:
        bucket = _ReleaseBucket(manifest=manifest or _manifest(), binary=binary)
        monkeypatch.setattr(
            claude_cli.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(bucket), **kw)
        )
        return bucket

    monkeypatch.setattr(claude_cli, "_platform", lambda: "linux-x64")
    monkeypatch.setattr(claude_cli.time, "sleep", lambda _s: None)
    return _install


@pytest.mark.parametrize(
    ("answers", "ranges"),
    [
        pytest.param(["full"], [None], id="first-try"),
        pytest.param(["half", "rest"], [None, f"bytes={len(_BUILD) // 2}-"], id="dropped-line-resumes"),
        pytest.param(["down", "down", "full"], [None, None, None], id="unreachable-twice-then-works"),
        pytest.param(["half", "full"], [None, f"bytes={len(_BUILD) // 2}-"], id="server-ignores-range"),
    ],
)
def test_install_downloads_the_pinned_build_once(
    tmp_path: Path, release_bucket: Callable[..., _ReleaseBucket], answers: list[str], ranges: list[str | None]
) -> None:
    bucket = release_bucket(binary=answers)
    progress: list[tuple[int, int]] = []
    data_dir = tmp_path / "data"

    binary = claude_cli.install(data_dir, on_progress=lambda done, total: progress.append((done, total)))

    assert binary == data_dir / "ai-subscription" / "claude-home" / ".local" / "bin" / "claude"
    target = claude_cli.claude_versions_dir(data_dir) / _VERSION
    assert binary.is_symlink() and binary.resolve() == target.resolve()
    assert target.read_bytes() == _BUILD
    assert target.stat().st_mode & 0o111
    assert bucket.ranges == ranges
    assert progress[-1] == (len(_BUILD), len(_BUILD))
    assert not list(target.parent.glob("*.part"))


def test_an_installed_claude_code_downloads_nothing(
    tmp_path: Path, release_bucket: Callable[..., _ReleaseBucket]
) -> None:
    bucket = release_bucket(binary=["full"])
    claude_cli.install(tmp_path / "data")

    claude_cli.install(tmp_path / "data")

    assert bucket.ranges == [None]


@pytest.mark.parametrize(
    ("answers", "manifest", "message"),
    [
        pytest.param(
            ["down"],
            None,
            r"^Could not download Claude Code from downloads\.claude\.ai \(timed out\)\. .*continues where it stopped\.$",
            id="never-reachable",
        ),
        pytest.param(
            ["full"],
            _manifest(checksum="0" * 64),
            r"^The downloaded Claude Code does not match Anthropic's checksum\. Run the command again\.$",
            id="checksum-mismatch",
        ),
        pytest.param(
            ["full"],
            _manifest(platform="darwin-arm64"),
            rf"^Anthropic's manifest for Claude Code {_VERSION} has no linux-x64 build\.$",
            id="no-build-for-this-machine",
        ),
    ],
)
def test_install_fails_with_a_reason_and_leaves_no_binary(
    tmp_path: Path,
    release_bucket: Callable[..., _ReleaseBucket],
    answers: list[str],
    manifest: dict[str, Any] | None,
    message: str,
) -> None:
    release_bucket(binary=answers, manifest=manifest)
    data_dir = tmp_path / "data"

    with pytest.raises(claude_cli.ClaudeInstallError, match=message):
        claude_cli.install(data_dir)

    assert not claude_cli.is_installed(data_dir)


def test_a_bad_download_is_thrown_away_not_resumed(tmp_path: Path, release_bucket: Callable[..., _ReleaseBucket]) -> None:
    release_bucket(binary=["full"], manifest=_manifest(checksum="0" * 64))
    data_dir = tmp_path / "data"

    with pytest.raises(claude_cli.ClaudeInstallError):
        claude_cli.install(data_dir)

    assert not list(claude_cli.claude_versions_dir(data_dir).glob("*.part"))


@pytest.mark.parametrize(
    "installed",
    [
        pytest.param(True, id="installed"),
        pytest.param(False, id="never-installed"),
    ],
)
def test_signing_out_forgets_the_last_limits_reading(tmp_path: Path, installed: bool) -> None:
    """A "limit reached" seen on one account must not pause the next sign-in, maybe another account."""
    data_dir = tmp_path / "data"
    limits = claude_cli.limits_path(data_dir)
    limits.parent.mkdir(parents=True)
    limits.write_text('{"status": "rejected", "windows": [{"window": "5h", "used_percent": 100}]}')
    binary = claude_cli.claude_binary(data_dir)
    if installed:
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\necho "$@" > "$(dirname "$0")/called-with"\n')
        binary.chmod(0o755)

    claude_cli.logout(data_dir)

    assert limits.parent.is_dir()
    assert not limits.exists()
    if installed:
        assert (binary.parent / "called-with").read_text().strip() == "auth logout"
