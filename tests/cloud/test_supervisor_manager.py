from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sellerclaw_agent.bundle.builder import BundleBuilder
from sellerclaw_agent.bundle.manifest import BundleManifest
from sellerclaw_agent.cloud.supervisor_manager import (
    REJECT_ALREADY_RUNNING,
    SupervisorContainerManager,
    _parse_uptime_seconds_from_line,
    create_supervisor_manager,
    write_runtime_env,
)

pytestmark = pytest.mark.unit


def _mgr(
    tmp_path: Path,
    agent_resources_root: Path,
    **kwargs: object,
) -> SupervisorContainerManager:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    defaults: dict[str, object] = {
        "bundle_builder": BundleBuilder(resources_root=agent_resources_root),
        "bundle_volume_path": bundle_dir,
        "display_name": "sellerclaw-openclaw",
        "program_name": "openclaw",
        "supervisord_config": "/etc/supervisor/conf.d/openclaw.conf",
        "gateway_host_port": 7788,
        "vnc_host_port": 6080,
        "runtime_image_tag": "sellerclaw-openclaw-runtime:test",
    }
    defaults.update(kwargs)
    return SupervisorContainerManager(**defaults)  # type: ignore[arg-type]


def test_probe_running_stopped_fatal_starting(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         RUNNING   pid 12, uptime 0:01:02\n",
            stderr="",
        )
        assert mgr.probe_openclaw_status() == ("running", None)

        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         STOPPED   Not started\n",
            stderr="",
        )
        assert mgr.probe_openclaw_status() == ("stopped", None)

        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         FATAL     Exited too quickly\n",
            stderr="",
        )
        st, err = mgr.probe_openclaw_status()
        assert st == "error"
        assert err is not None and "FATAL" in err

        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         STARTING  \n",
            stderr="",
        )
        assert mgr.probe_openclaw_status() == ("starting", None)

        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         BACKOFF   Exited too quickly\n",
            stderr="",
        )
        st2, err2 = mgr.probe_openclaw_status()
        assert st2 == "error"
        assert err2 is not None


def test_probe_exited_maps_to_stopped(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         EXITED    Jan 01 01:02 PM\n",
            stderr="",
        )
        assert mgr.probe_openclaw_status() == ("stopped", None)


def test_probe_empty_stdout_is_error(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(returncode=3, stdout="", stderr="refused connection")
        st, err = mgr.probe_openclaw_status()
        assert st == "error"
        assert err is not None and "refused" in err


def test_probe_subprocess_timeout(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.side_effect = TimeoutError("boom")
        st, err = mgr.probe_openclaw_status()
        assert st == "error"
        assert err is not None and "boom" in err


def test_start_writes_bundle_and_supervisorctl_start(
    tmp_path: Path,
    agent_resources_root: Path,
    make_manifest: Callable[..., BundleManifest],
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    manifest = make_manifest()

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd:
            return MagicMock(
                returncode=0,
                stdout="openclaw                         STOPPED   Not started\n",
                stderr="",
            )
        if "start" in cmd:
            return MagicMock(returncode=0, stdout="started\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.start(manifest)
    assert outcome == "completed"
    assert err is None
    assert (mgr.bundle_volume_path / "openclaw" / "openclaw.json").is_file()
    # runtime.env is written alongside bundle so shell scripts pick manifest values up.
    assert (mgr.bundle_volume_path / "runtime.env").is_file()


def test_write_runtime_env_exports_proxy_url(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    write_runtime_env(bundle_dir, proxy_url="http://u:p@proxy.example:3128")
    body = (bundle_dir / "runtime.env").read_text(encoding="utf-8")
    assert "export PROXY_URL='http://u:p@proxy.example:3128'" in body


def test_write_runtime_env_escapes_single_quotes(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    write_runtime_env(bundle_dir, proxy_url="http://o'neil:x@proxy:3128")
    body = (bundle_dir / "runtime.env").read_text(encoding="utf-8")
    assert "export PROXY_URL='http://o'\\''neil:x@proxy:3128'" in body


def test_write_runtime_env_empty_proxy(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    write_runtime_env(bundle_dir, proxy_url="")
    body = (bundle_dir / "runtime.env").read_text(encoding="utf-8")
    assert "export PROXY_URL=''" in body


def test_start_rejects_when_running(
    tmp_path: Path,
    agent_resources_root: Path,
    make_manifest: Callable[..., BundleManifest],
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         RUNNING   pid 1, uptime 0:00:01\n",
            stderr="",
        )
        outcome, err = mgr.start(make_manifest())
    assert outcome == "rejected"
    assert err == REJECT_ALREADY_RUNNING


def test_start_maps_already_started_to_rejected(
    tmp_path: Path,
    agent_resources_root: Path,
    make_manifest: Callable[..., BundleManifest],
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd:
            return MagicMock(
                returncode=0,
                stdout="openclaw                         STOPPED   Not started\n",
                stderr="",
            )
        return MagicMock(
            returncode=7,
            stdout="",
            stderr="openclaw: ERROR (already started)\n",
        )

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.start(make_manifest())
    assert outcome == "rejected"
    assert err == REJECT_ALREADY_RUNNING


def test_start_supervisorctl_failure(
    tmp_path: Path,
    agent_resources_root: Path,
    make_manifest: Callable[..., BundleManifest],
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd:
            return MagicMock(
                returncode=0,
                stdout="openclaw                         STOPPED   Not started\n",
                stderr="",
            )
        return MagicMock(returncode=1, stdout="", stderr="spawn error")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.start(make_manifest())
    assert outcome == "failed"
    assert err is not None and "spawn error" in err


def test_stop_success_and_idempotent_not_running(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="stopped\n", stderr="")
        assert mgr.stop() == ("completed", None)

        run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="openclaw: ERROR (not running)\n",
        )
        assert mgr.stop() == ("completed", None)


def test_stop_failure(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
        outcome, err = mgr.stop()
    assert outcome == "failed"
    assert err is not None and "permission" in err


def test_restart_writes_bundle_and_calls_restart(
    tmp_path: Path,
    agent_resources_root: Path,
    make_manifest: Callable[..., BundleManifest],
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    manifest = make_manifest()
    calls: list[list[str]] = []

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        calls.append(list(cmd))
        if "restart" in cmd:
            return MagicMock(returncode=0, stdout="restarted\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.restart(manifest)
    assert outcome == "completed"
    assert err is None
    assert any("restart" in c for c in calls)
    assert (mgr.bundle_volume_path / "openclaw" / "openclaw.json").is_file()


def test_restart_supervisorctl_failure(
    tmp_path: Path,
    agent_resources_root: Path,
    make_manifest: Callable[..., BundleManifest],
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "restart" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="restart failed")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.restart(make_manifest())
    assert outcome == "failed"
    assert err is not None and "restart failed" in err


def test_get_status_detail_running_with_uptime(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         RUNNING   pid 99, uptime 0:05:07\n",
            stderr="",
        )
        d = mgr.get_status_detail()
    assert d["status"] == "running"
    assert d["container_id"] == "99"
    assert d["container_name"] == "sellerclaw-openclaw"
    assert d["uptime_seconds"] == 5 * 60 + 7
    assert d["ports"] == {"gateway": 7788, "vnc": 6080}
    assert d["error"] is None


def test_get_status_detail_stopped(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         STOPPED   Not started\n",
            stderr="",
        )
        d = mgr.get_status_detail()
    assert d["status"] == "stopped"
    assert d["container_id"] is None
    assert d["uptime_seconds"] is None


def test_get_status_detail_starting(
    tmp_path: Path,
    agent_resources_root: Path,
) -> None:
    mgr = _mgr(tmp_path, agent_resources_root)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         STARTING  \n",
            stderr="",
        )
        d = mgr.get_status_detail()
    assert d["status"] == "starting"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        pytest.param(
            "openclaw  RUNNING  pid 1, uptime 0:05:07",
            307.0,
            id="h-m-s-short",
        ),
        pytest.param(
            "openclaw  RUNNING  pid 1, uptime 1:02:03",
            3723.0,
            id="h-m-s-over-hour",
        ),
        pytest.param(
            "openclaw  RUNNING  pid 1, uptime 1 day, 2:03:04",
            86400 + 2 * 3600 + 3 * 60 + 4,
            id="1-day",
        ),
        pytest.param(
            "openclaw  RUNNING  pid 1, uptime 7 days, 0:05:07",
            7 * 86400 + 5 * 60 + 7,
            id="7-days",
        ),
        pytest.param(
            "openclaw  RUNNING  pid 1, uptime 0:42",
            42.0,
            id="m-s-only",
        ),
        pytest.param(
            "openclaw  STOPPED  Not started",
            None,
            id="no-uptime",
        ),
    ],
)
def test_parse_uptime_seconds_from_line(line: str, expected: float | None) -> None:
    assert _parse_uptime_seconds_from_line(line) == expected


def test_create_supervisor_manager_uses_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENCLAW_BUNDLE_VOLUME_PATH", str(tmp_path / "b"))
    monkeypatch.setenv("OPENCLAW_CONTAINER_NAME", "my-oc")
    monkeypatch.setenv("OPENCLAW_SUPERVISOR_PROGRAM", "ocprog")
    monkeypatch.setenv("OPENCLAW_SUPERVISOR_CONF", "/tmp/s.conf")
    monkeypatch.setenv("OPENCLAW_PORT_GATEWAY", "7789")
    monkeypatch.setenv("OPENCLAW_PORT_VNC", "6081")
    monkeypatch.setenv("OPENCLAW_RUNTIME_IMAGE", "img:tag")
    (tmp_path / "b").mkdir()

    m = create_supervisor_manager()
    assert m.bundle_volume_path == tmp_path / "b"
    assert m.display_name == "my-oc"
    assert m.program_name == "ocprog"
    assert m.supervisord_config == "/tmp/s.conf"
    assert m.gateway_host_port == 7789
    assert m.vnc_host_port == 6081
    assert m.runtime_image_tag == "img:tag"
