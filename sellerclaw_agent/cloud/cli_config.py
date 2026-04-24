from __future__ import annotations

import os
from pathlib import Path

import structlog

from sellerclaw_agent.cloud.settings import get_sellerclaw_api_url

_log = structlog.get_logger(__name__)


def _config_path() -> Path:
    """Where ``sellerclaw-cli`` looks up auth when no env vars are set.

    Uses ``$HOME`` so the config sits under the user running the process
    (``node`` in the edge container). ``docker exec -u node`` finds it; a root
    ``docker exec`` does not — same rule as any ``~/.config`` tool.
    """
    return Path(os.environ.get("HOME", "/home/node")) / ".config" / "sellerclaw" / "config.toml"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def write_cli_config(*, token: str, api_url: str | None = None) -> None:
    """Mirror the agent's ``sca_`` token to ``sellerclaw-cli``'s config file.

    The edge image ships the ``sellerclaw`` CLI so agents can call the API
    from exec-tool. The CLI reads ``~/.config/sellerclaw/config.toml`` when
    ``SELLERCLAW_TOKEN`` env is not set — that path works from any shell in
    the container (including a fresh ``docker exec``) without inheriting
    process env from supervisord/openclaw.
    """
    resolved_api_url = api_url if api_url is not None else get_sellerclaw_api_url()
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "# Managed by sellerclaw-agent. Regenerated whenever credentials change.\n"
            f'api_url = "{_escape(resolved_api_url)}"\n'
            f'token = "{_escape(token)}"\n'
        )
        tmp = path.with_suffix(".toml.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, path)
    except OSError as exc:
        _log.warning("sellerclaw_cli_config_write_failed", path=str(path), error=str(exc))
        return
    _log.info("sellerclaw_cli_config_written", path=str(path))


def remove_cli_config() -> None:
    """Best-effort removal of the CLI auth file (ignore missing / perm errors)."""
    path = _config_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("sellerclaw_cli_config_remove_failed", path=str(path), error=str(exc))
        return
    _log.info("sellerclaw_cli_config_removed", path=str(path))
