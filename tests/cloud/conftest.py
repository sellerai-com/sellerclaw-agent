from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_home_for_cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``$HOME`` to ``tmp_path`` for every cloud test.

    ``CredentialsStorage.save()`` / ``.clear()`` now mirror the token into the
    sellerclaw-cli config at ``~/.config/sellerclaw/config.toml``. Without this
    fixture, unit tests that exercise those methods would write into the
    developer's real home directory.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    yield tmp_path
