from __future__ import annotations

from pathlib import Path

import pytest
from sellerclaw_agent.cloud.cli_config import remove_cli_config, write_cli_config

pytestmark = pytest.mark.unit


def _config_file(home: Path) -> Path:
    return home / ".config" / "sellerclaw" / "config.toml"


@pytest.mark.parametrize(
    ("token", "api_url", "expected_token_line", "expected_url_line"),
    [
        pytest.param(
            "sca_abcdef",
            "https://api.sellerclaw.com",
            'token = "sca_abcdef"',
            'api_url = "https://api.sellerclaw.com"',
            id="plain-values",
        ),
        pytest.param(
            'sca_a"b\\c',
            "https://api/x",
            'token = "sca_a\\"b\\\\c"',
            'api_url = "https://api/x"',
            id="token-with-special-chars-escaped",
        ),
    ],
)
def test_write_cli_config_creates_file_with_600_perms(
    tmp_path: Path,
    token: str,
    api_url: str,
    expected_token_line: str,
    expected_url_line: str,
) -> None:
    write_cli_config(token=token, api_url=api_url)
    path = _config_file(tmp_path)
    assert path.is_file()
    body = path.read_text(encoding="utf-8")
    assert expected_url_line in body
    assert expected_token_line in body
    assert (path.stat().st_mode & 0o777) == 0o600


def test_write_cli_config_overwrites_existing(tmp_path: Path) -> None:
    write_cli_config(token="sca_old", api_url="https://old")
    write_cli_config(token="sca_new", api_url="https://new")
    body = _config_file(tmp_path).read_text(encoding="utf-8")
    assert 'token = "sca_new"' in body
    assert "sca_old" not in body
    assert 'api_url = "https://new"' in body


def test_remove_cli_config_deletes_file(tmp_path: Path) -> None:
    write_cli_config(token="sca_x", api_url="https://x")
    path = _config_file(tmp_path)
    assert path.is_file()
    remove_cli_config()
    assert not path.exists()


def test_remove_cli_config_is_noop_when_missing(tmp_path: Path) -> None:
    assert not _config_file(tmp_path).exists()
    remove_cli_config()  # must not raise


def test_write_cli_config_swallows_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Point HOME at a path that cannot be created (file where a dir is expected).
    blocker = tmp_path / "home_is_a_file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("HOME", str(blocker))
    # Must not raise — sync failures are not allowed to break auth flows.
    write_cli_config(token="sca_x", api_url="https://x")
