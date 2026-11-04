"""Cron standby: an agent instance another one replaced stops running and reporting the owner's jobs.

The cloud keeps one edge session per owner. When ``connect`` is refused with
``agent_connection_already_active``, another instance (a recreated managed machine, the agent
started on a second computer) holds it — and both machines run the same OpenClaw cron jobs, since
the cloud restores the owner's jobs onto every fresh store. So the refused instance goes on
standby: the ``sellerclaw-ui`` plugin disables the jobs the cloud mirrors and stops posting
anything cron-related (snapshot, runs, restore) — two instances with different store epochs
posting in turn would make the cloud restore every job onto both and overwrite the real enabled
state with the replaced one's. A revoked agent token (401) means the same: this machine will never
act for the owner again.

A successful ping means this instance holds the session; "active" is said on every one of them.
The plugin reports nothing until it hears it in its own process (a laptop waking up after another
instance took over learns that only a ping later), and a restarted OpenClaw hears it again within
a ping. Coming back from standby, the plugin drops the jobs it mirrors and the cloud hands back
the owner's current set.

The standby wish is persisted (``cron_standby.json`` in the data dir) so a restarted agent server
keeps it; the plugin persists its own side next to the cron store.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

import httpx
import structlog

from sellerclaw_agent.cloud.openclaw_forwarder import LocalOpenClawForwarder, openclaw_gateway_base_url
from sellerclaw_agent.server.secrets_store import get_secrets

_log = structlog.get_logger(__name__)

STANDBY_FILE_NAME: Final[str] = "cron_standby.json"
#: Disabling or re-enabling a handful of jobs is quick; a slow gateway is retried next ping.
RECONCILE_TIMEOUT_S: Final[float] = 20.0


class CronStandbyPlugin(Protocol):
    async def post_cron_standby_json(self, *, standby: bool) -> tuple[bool, dict[str, Any]]: ...


PluginFactory = Callable[[], CronStandbyPlugin]


def default_plugin_factory(data_dir: Path) -> PluginFactory:
    def _factory() -> CronStandbyPlugin:
        secrets = get_secrets(data_dir)
        return LocalOpenClawForwarder(
            base_url=openclaw_gateway_base_url(),
            hooks_token=secrets.hooks_token,
            gateway_token=secrets.gateway_token,
        )

    return _factory


class CronStandby:
    """The desired standby state of this instance and its delivery to the plugin."""

    def __init__(self, data_dir: Path, *, plugin_factory: PluginFactory | None = None) -> None:
        self._path = data_dir / STANDBY_FILE_NAME
        self._plugin_factory = plugin_factory or default_plugin_factory(data_dir)
        # What the plugin last confirmed; unknown in a fresh process, so the wish is sent once.
        self._acked: bool | None = None

    def is_standby(self) -> bool:
        return self._path.exists()

    def request_standby(self) -> None:
        """Another instance holds the session: this one must stop running and reporting jobs."""
        if self.is_standby():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"since": datetime.now(UTC).isoformat()}), encoding="utf-8")
        os.replace(tmp, self._path)
        self._acked = None
        _log.warning("cron_standby_requested")

    def request_active(self) -> None:
        """This instance holds the session: its jobs run and are reported again."""
        if not self.is_standby():
            return
        self._path.unlink(missing_ok=True)
        self._acked = None
        _log.warning("cron_standby_released")

    async def reconcile(self) -> bool:
        """Bring the plugin to the desired state; True once it confirmed. Never raises.

        Standby is sent until the plugin confirmed it; "active" on every call — it is the plugin's
        go-ahead to report, and an OpenClaw restarted meanwhile has forgotten it.
        """
        desired = self.is_standby()
        if desired and self._acked is True:
            return True
        try:
            plugin = self._plugin_factory()
            held, payload = await asyncio.wait_for(
                plugin.post_cron_standby_json(standby=desired), timeout=RECONCILE_TIMEOUT_S
            )
        except (httpx.HTTPError, OSError, TimeoutError, ValueError) as exc:
            _log.info("cron_standby_not_delivered", standby=desired, error=str(exc)[:300])
            return False
        if not held:
            _log.info("cron_standby_not_held_yet", standby=desired, detail=str(payload.get("error"))[:300])
            return False
        self._acked = desired
        _log.info("cron_standby_delivered", standby=desired, job_ids=payload.get("job_ids"))
        return True


_INSTANCES: dict[Path, CronStandby] = {}


def get_cron_standby(data_dir: Path | None = None) -> CronStandby:
    """The process-wide standby state for ``data_dir`` (default ``SELLERCLAW_DATA_DIR``)."""
    resolved = data_dir or Path(os.environ.get("SELLERCLAW_DATA_DIR", "/data"))
    instance = _INSTANCES.get(resolved)
    if instance is None:
        instance = CronStandby(resolved)
        _INSTANCES[resolved] = instance
    return instance
