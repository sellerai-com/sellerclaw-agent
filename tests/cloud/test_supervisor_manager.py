from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sellerclaw_agent.bundle.builder import BundleBuilder
from sellerclaw_agent.bundle.manifest import GenericManifest
from sellerclaw_agent.cloud.supervisor_manager import (
    REJECT_ALREADY_RUNNING,
    REJECT_OPENCLAW_RUNNING_BROWSER,
    SupervisorContainerManager,
    _is_ready_payload,
    _parse_uptime_seconds_from_line,
    bundle_on_disk_matches,
    create_supervisor_manager,
    read_proxy_url_from_runtime_env,
    write_runtime_env,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def with_agent_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``BundleBuilder`` requires an agent API key for ``sellerclaw-ui`` (webhook + plugin config)."""
    monkeypatch.setenv("AGENT_API_KEY", "sca_test_supervisor_bundle_key")


def _mgr(
    tmp_path: Path,
    **kwargs: object,
) -> SupervisorContainerManager:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    defaults: dict[str, object] = {
        "bundle_builder": BundleBuilder(),
        "bundle_volume_path": bundle_dir,
        "display_name": "sellerclaw-openclaw",
        "program_name": "openclaw",
        "supervisord_config": "/etc/supervisor/conf.d/openclaw.conf",
        "gateway_host_port": 7788,
        "vnc_host_port": 6080,
        "runtime_image_tag": "sellerclaw-openclaw-runtime:test",
        "kasm_program_name": "kasmvnc",
        "gost_program_name": "gost",
        "credentials_data_dir": tmp_path,
    }
    defaults.update(kwargs)
    return SupervisorContainerManager(**defaults)  # type: ignore[arg-type]


def test_probe_running_stopped_fatal_starting(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)
    with (
        patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run,
        patch.object(mgr, "_gateway_is_ready", return_value=True),
    ):
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


def test_probe_supervisor_running_but_gateway_not_ready_is_starting(
    tmp_path: Path,
) -> None:
    """Supervisor reports RUNNING before openclaw HTTP listener is up; probe must downgrade to starting.

    Without this, the cloud would forward user chat messages into the local
    OpenClaw inbound endpoint while the gateway HTTP server is still booting,
    and they would drop with ``httpx.ConnectError``.
    """
    mgr = _mgr(tmp_path)
    with (
        patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run,
        patch.object(mgr, "_gateway_is_ready", return_value=False),
    ):
        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         RUNNING   pid 12, uptime 0:00:03\n",
            stderr="",
        )
        assert mgr.probe_openclaw_status() == ("starting", None)


@pytest.mark.parametrize(
    ("raised", "expected", "why"),
    [
        pytest.param(
            TimeoutError("timed out"),
            True,
            "a busy listener is still a listener",
            id="read-timeout",
        ),
        pytest.param(
            urllib.error.URLError(TimeoutError("timed out")),
            True,
            "the same timeout, raised while the connection was being set up",
            id="timeout-wrapped-in-urlerror",
        ),
        pytest.param(
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
            False,
            "nothing is listening on the port yet",
            id="connection-refused",
        ),
        pytest.param(
            OSError("host unreachable"),
            False,
            "the socket itself failed",
            id="socket-error",
        ),
    ],
)
def test_a_slow_ready_probe_does_not_mean_the_gateway_is_down(
    tmp_path: Path,
    raised: Exception,
    expected: bool,
    why: str,
) -> None:
    """Only an answer showing the listener is absent counts as not ready — being slow does not.

    OpenClaw runs one event loop, so a long tool call keeps it from answering a 1.5-second health
    check. Reading that as "still starting" made the edge drop the owner's next chat message: they
    asked for an ad campaign, the agent was told nothing, and replied that no such request existed.
    """
    mgr = _mgr(tmp_path)
    with patch(
        "sellerclaw_agent.cloud.supervisor_manager.urllib.request.urlopen", side_effect=raised
    ):
        assert mgr._gateway_is_ready() is expected, why


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param('{"ready": true, "failing": []}', True, id="ready-true"),
        pytest.param('{"ready": false, "failing": ["plugins"]}', False, id="ready-false"),
        pytest.param('{"failing": []}', False, id="missing-ready-key"),
        pytest.param("not-json", False, id="invalid-json"),
        pytest.param("[]", False, id="json-array-not-object"),
        pytest.param("", False, id="empty-body"),
    ],
)
def test_is_ready_payload(body: str, expected: bool) -> None:
    assert _is_ready_payload(body) is expected


def test_probe_exited_maps_to_stopped(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="openclaw                         EXITED    Jan 01 01:02 PM\n",
            stderr="",
        )
        assert mgr.probe_openclaw_status() == ("stopped", None)


def test_probe_empty_stdout_is_error(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(returncode=3, stdout="", stderr="refused connection")
        st, err = mgr.probe_openclaw_status()
        assert st == "error"
        assert err is not None and "refused" in err


def test_probe_subprocess_timeout(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.side_effect = TimeoutError("boom")
        st, err = mgr.probe_openclaw_status()
        assert st == "error"
        assert err is not None and "boom" in err


def test_start_writes_bundle_and_supervisorctl_start(
    tmp_path: Path,
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    mgr = _mgr(tmp_path)
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
    # Shared skills are embedded under each agent workspace (no separate shared-skills dir).
    ws_skill = mgr.bundle_volume_path / "workspaces" / "supervisor" / "skills" / "task-management" / "SKILL.md"
    assert ws_skill.is_file()
    assert ws_skill.read_text(encoding="utf-8").strip()


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
    make_manifest: Callable[..., GenericManifest],
) -> None:
    mgr = _mgr(tmp_path)
    with (
        patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run,
        patch.object(mgr, "_gateway_is_ready", return_value=True),
    ):
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
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    mgr = _mgr(tmp_path)

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
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    mgr = _mgr(tmp_path)

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
) -> None:
    mgr = _mgr(tmp_path)
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
) -> None:
    mgr = _mgr(tmp_path)
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
        outcome, err = mgr.stop()
    assert outcome == "failed"
    assert err is not None and "permission" in err


def test_restart_writes_bundle_and_calls_restart(
    tmp_path: Path,
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    mgr = _mgr(tmp_path)
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


def test_update_manifest_writes_bundle_and_activates_without_restart(
    tmp_path: Path,
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    """Happy path: hot-reload writes the bundle and activates it via ``apply-bundle``.

    It must materialise the staged bundle into the live config/workspaces (so the
    change actually reaches the running gateway) but must NOT restart/start the
    gateway via supervisorctl — that's what keeps it a hot-reload.
    """
    mgr = _mgr(tmp_path)
    manifest = make_manifest(proxy_url="")
    calls: list[list[str]] = []

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        calls.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.update_manifest(manifest)

    assert outcome == "completed"
    assert err is None
    # Activation runs `openclaw_start apply-bundle` — and nothing else.
    assert calls == [[mgr.openclaw_start_cmd, "apply-bundle"]]
    # Crucially: no supervisorctl restart/start. The whole point of hot-reload.
    assert not any("supervisorctl" in c[0] for c in calls)
    assert (mgr.bundle_volume_path / "openclaw" / "openclaw.json").is_file()


def test_update_manifest_fails_when_apply_bundle_errors(
    tmp_path: Path,
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    """A non-zero ``apply-bundle`` surfaces as a failed command, not a silent success.

    Otherwise the cloud would mark the change applied while the live config stays stale.
    """
    mgr = _mgr(tmp_path)
    manifest = make_manifest(proxy_url="")

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        return MagicMock(returncode=3, stdout="", stderr="materialize failed\n")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.update_manifest(manifest)

    assert outcome == "failed"
    assert err is not None and "materialize failed" in err
    # The bundle was still staged to disk; only activation failed.
    assert (mgr.bundle_volume_path / "openclaw" / "openclaw.json").is_file()


def test_update_manifest_rejects_proxy_change(
    tmp_path: Path,
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    """Proxy lives in ``runtime.env`` and supervisord only reads it at start.

    The cloud classifier sends ``BROWSER_PROXY_CHANGED`` down the RESTART path,
    but the edge double-checks here: if the manifest's proxy differs from the
    currently baked value we refuse the hot-reload so the user isn't silently
    left with the old proxy.
    """
    mgr = _mgr(tmp_path)
    write_runtime_env(mgr.bundle_volume_path, proxy_url="http://old-proxy:3128")

    manifest = make_manifest(proxy_url="http://new-proxy:3128")
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
        outcome, err = mgr.update_manifest(manifest)
    assert outcome == "failed"
    assert err is not None and "proxy" in err
    # No subprocess call attempted; the bundle wasn't rebuilt either.
    run.assert_not_called()


def test_update_manifest_skips_write_when_bundle_unchanged(
    tmp_path: Path,
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    """Two debounced applies in a row with identical manifests → second is a no-op.

    Skipping the write keeps OpenClaw's file-watcher quiet (no validation noise
    in the gateway log on every idempotent setting save).
    """
    mgr = _mgr(tmp_path)
    manifest = make_manifest(proxy_url="")
    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", return_value=MagicMock(returncode=0)):
        first = mgr.update_manifest(manifest)
    assert first == ("completed", None)

    oc_path = mgr.bundle_volume_path / "openclaw" / "openclaw.json"
    mtime_before = oc_path.stat().st_mtime_ns

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", return_value=MagicMock(returncode=0)):
        second = mgr.update_manifest(manifest)
    assert second == ("completed", None)
    # File untouched on the second pass.
    assert oc_path.stat().st_mtime_ns == mtime_before


def test_update_manifest_writes_applied_config_version(
    tmp_path: Path,
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a successful apply the agent records the applied config_version in the sidecar
    state file it reports in pings (it must NOT go into openclaw.json — OpenClaw rejects
    unknown meta keys)."""
    from sellerclaw_agent.cloud.state_backup import read_applied_config_version

    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    mgr = _mgr(tmp_path)
    manifest = make_manifest(proxy_url="", overrides={"config_version": 7})

    with patch(
        "sellerclaw_agent.cloud.supervisor_manager.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="", stderr=""),
    ):
        outcome, err = mgr.update_manifest(manifest)

    assert outcome == "completed"
    assert err is None
    assert read_applied_config_version(tmp_path) == 7


def test_bundle_on_disk_matches_detects_workspace_drift(tmp_path: Path) -> None:
    """Spot-check the helper directly so callers can rely on cheap equality."""
    bundle = tmp_path / "bundle"
    (bundle / "openclaw").mkdir(parents=True)
    (bundle / "openclaw" / "openclaw.json").write_text("{}", encoding="utf-8")
    (bundle / "workspaces" / "supervisor").mkdir(parents=True)
    (bundle / "workspaces" / "supervisor" / "AGENTS.md").write_text("hello", encoding="utf-8")

    assert bundle_on_disk_matches(
        bundle,
        openclaw_config="{}",
        workspaces={"supervisor/AGENTS.md": "hello"},
    )
    assert not bundle_on_disk_matches(
        bundle,
        openclaw_config="{}",
        workspaces={"supervisor/AGENTS.md": "world"},
    )
    assert not bundle_on_disk_matches(
        bundle,
        openclaw_config='{"changed": true}',
        workspaces={"supervisor/AGENTS.md": "hello"},
    )


def test_read_proxy_url_from_runtime_env_round_trips(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    write_runtime_env(bundle, proxy_url="http://o'neil:x@proxy:3128")
    assert read_proxy_url_from_runtime_env(bundle) == "http://o'neil:x@proxy:3128"

    write_runtime_env(bundle, proxy_url="")
    assert read_proxy_url_from_runtime_env(bundle) == ""

    missing = tmp_path / "missing"
    assert read_proxy_url_from_runtime_env(missing) is None


def test_restart_supervisorctl_failure(
    tmp_path: Path,
    make_manifest: Callable[..., GenericManifest],
    with_agent_api_key: None,
) -> None:
    mgr = _mgr(tmp_path)

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
) -> None:
    mgr = _mgr(tmp_path)
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
) -> None:
    mgr = _mgr(tmp_path)
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
) -> None:
    mgr = _mgr(tmp_path)
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


def test_probe_browser_status_kasm_stopped(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd and mgr.kasm_program_name in cmd:
            return MagicMock(
                returncode=0,
                stdout=f"{mgr.kasm_program_name}                         STOPPED   Not started\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        b = mgr.probe_browser_status()
    assert b.status == "stopped"
    assert b.kasmvnc_running is False
    assert b.chrome_running is False
    assert b.pages is None


def test_probe_browser_status_kasm_starting(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd and mgr.kasm_program_name in cmd:
            return MagicMock(
                returncode=0,
                stdout=f"{mgr.kasm_program_name}                         STARTING  \n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        b = mgr.probe_browser_status()
    assert b.status == "starting"
    assert b.kasmvnc_running is True


def test_probe_browser_cdp_timeout_short_uptime_is_starting(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd and mgr.kasm_program_name in cmd:
            return MagicMock(
                returncode=0,
                stdout=f"{mgr.kasm_program_name}                         RUNNING   pid 1, uptime 0:00:02\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    def urlopen_side_effect(*_a: object, **_kw: object) -> None:
        raise TimeoutError("cdp")

    with (
        patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect),
        patch("sellerclaw_agent.cloud.supervisor_manager.urllib.request.urlopen", side_effect=urlopen_side_effect),
    ):
        b = mgr.probe_browser_status()
    assert b.status == "starting"


def test_probe_browser_cdp_timeout_long_uptime_is_error(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd and mgr.kasm_program_name in cmd:
            return MagicMock(
                returncode=0,
                stdout=f"{mgr.kasm_program_name}                         RUNNING   pid 1, uptime 0:10:00\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    def urlopen_side_effect(*_a: object, **_kw: object) -> None:
        raise TimeoutError("cdp")

    with (
        patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect),
        patch("sellerclaw_agent.cloud.supervisor_manager.urllib.request.urlopen", side_effect=urlopen_side_effect),
    ):
        b = mgr.probe_browser_status()
    assert b.status == "error"
    assert b.error is not None


def test_probe_browser_running_with_page_targets(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)
    cdp_payload = [
        {"type": "page", "url": "https://seller.shopify.com/orders", "title": "Orders"},
        {"type": "service_worker", "url": "ignored"},
    ]

    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps(cdp_payload).encode("utf-8")

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd and mgr.kasm_program_name in cmd:
            return MagicMock(
                returncode=0,
                stdout=f"{mgr.kasm_program_name}                         RUNNING   pid 1, uptime 0:01:00\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect),
        patch(
            "sellerclaw_agent.cloud.supervisor_manager.urllib.request.urlopen",
            return_value=_Resp(),
        ),
    ):
        b = mgr.probe_browser_status()
    assert b.status == "running"
    assert b.kasmvnc_running is True
    assert b.chrome_running is True
    assert b.pages is not None and len(b.pages) == 1
    assert b.pages[0].url.startswith("https://seller.shopify.com")
    assert b.pages[0].title == "Orders"


def test_probe_browser_cdp_ok_no_page_targets_long_uptime_is_running(
    tmp_path: Path,
) -> None:
    """Kasm RUNNING + reachable CDP + zero page targets after warm-up → running (not stopped)."""
    mgr = _mgr(tmp_path)

    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return b"[]"

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if "status" in cmd and mgr.kasm_program_name in cmd:
            return MagicMock(
                returncode=0,
                stdout=f"{mgr.kasm_program_name}                         RUNNING   pid 1, uptime 0:10:00\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect),
        patch(
            "sellerclaw_agent.cloud.supervisor_manager.urllib.request.urlopen",
            return_value=_Resp(),
        ),
    ):
        b = mgr.probe_browser_status()
    assert b.status == "running"
    assert b.kasmvnc_running is True
    assert b.chrome_running is False
    assert b.pages is None


def test_open_browser_rejected_when_openclaw_running(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)
    with patch.object(mgr, "probe_openclaw_status", return_value=("running", None)):
        outcome, err = mgr.open_browser()
    assert outcome == "rejected"
    assert err == REJECT_OPENCLAW_RUNNING_BROWSER


def test_open_browser_starts_sidecars_and_chrome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _mgr(tmp_path)
    sock = tmp_path / "X1"
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_bytes(b"")
    monkeypatch.setenv("OPENCLAW_BROWSER_X11_SOCKET", str(sock))
    monkeypatch.setenv("OPENCLAW_CHROME_LAUNCHER", "/bin/true")

    with patch.object(mgr, "probe_openclaw_status", return_value=("stopped", None)):
        with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.Popen") as popen:
                popen.return_value = MagicMock()
                outcome, err = mgr.open_browser()
    assert outcome == "completed"
    assert err is None
    ctl_cmds = [c.args[0] for c in run.call_args_list if c.args and c.args[0][:1] == ["supervisorctl"]]
    assert any("start" in c and mgr.kasm_program_name in c for c in ctl_cmds)
    assert any("start" in c and mgr.gost_program_name in c for c in ctl_cmds)
    popen.assert_called_once()


def test_close_browser_idempotent(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if cmd and cmd[0] == "/bin/sh":
            return MagicMock(returncode=0, stdout="", stderr="")
        if "stop" in cmd:
            return MagicMock(returncode=0, stdout="stopped\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        assert mgr.close_browser() == ("completed", None)


def _capture_close_browser(
    mgr: SupervisorContainerManager,
) -> tuple[tuple[str, str | None], list[list[str]], str]:
    """Run ``close_browser`` with a mocked subprocess; return result + commands.

    Returns ``(outcome, ctl_cmds, cleanup_script)`` where ``ctl_cmds`` are the
    ``supervisorctl`` argv lists and ``cleanup_script`` is the ``/bin/sh -c``
    body.
    """
    ctl_cmds: list[list[str]] = []
    cleanup_scripts: list[str] = []

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if cmd and cmd[0] == "/bin/sh":
            cleanup_scripts.append(cmd[2])
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd and cmd[0] == "supervisorctl":
            ctl_cmds.append(cmd)
            return MagicMock(returncode=0, stdout="stopped\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome = mgr.close_browser()
    assert len(cleanup_scripts) == 1
    return outcome, ctl_cmds, cleanup_scripts[0]


def test_close_browser_kills_orphan_xvnc_and_clears_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/home/node")
    monkeypatch.delenv("OPENCLAW_BROWSER_DISPLAY", raising=False)
    monkeypatch.delenv("OPENCLAW_BROWSER_PROFILE_DIR", raising=False)
    mgr = _mgr(tmp_path)

    outcome, ctl_cmds, script = _capture_close_browser(mgr)

    assert outcome == ("completed", None)
    # Supervised wrapper is stopped first so autorestart can't relaunch it.
    assert any("stop" in c and mgr.kasm_program_name in c for c in ctl_cmds)
    assert any("stop" in c and mgr.gost_program_name in c for c in ctl_cmds)
    # The orphaned X server (display :1) is killed explicitly. The ``[X]``
    # bracket trick stops pkill -f from matching this script's own cmdline.
    assert "pkill -f '[X]vnc :1 '" in script
    assert "pkill -f '[v]ncserver :1 '" in script
    # Stale display locks + Chrome singleton locks are removed for a clean reopen.
    assert "/tmp/.X11-unix/X1" in script
    assert "/tmp/.X1-lock" in script
    assert "/home/node/.openclaw/browser/openclaw/user-data'/Singleton*" in script
    # Chrome itself is still closed.
    assert "pkill -f '[g]oogle-chrome-stable'" in script
    # No pkill pattern may appear as a plain literal that would match this
    # script's own command line (self-kill guard).
    assert "pkill -f Xvnc" not in script
    assert "pkill -f google-chrome-stable" not in script


def test_close_browser_honors_custom_display_and_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_BROWSER_DISPLAY", ":7")
    monkeypatch.setenv("OPENCLAW_BROWSER_PROFILE_DIR", "/custom/profile")
    mgr = _mgr(tmp_path)

    outcome, _ctl_cmds, script = _capture_close_browser(mgr)

    assert outcome == ("completed", None)
    assert "pkill -f '[X]vnc :7 '" in script
    assert "/tmp/.X11-unix/X7" in script
    assert "/tmp/.X7-lock" in script
    assert "'/custom/profile'/Singleton*" in script


def test_close_browser_runs_cleanup_even_when_stop_fails(
    tmp_path: Path,
) -> None:
    mgr = _mgr(tmp_path)
    cleanup_scripts: list[str] = []

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if cmd and cmd[0] == "/bin/sh":
            cleanup_scripts.append(cmd[2])
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd and cmd[0] == "supervisorctl":
            return MagicMock(returncode=1, stdout="", stderr="ERROR: gone\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.close_browser()

    assert outcome == "failed"
    assert err is not None and "gone" in err
    # Cleanup still ran despite the supervisor stop failure.
    assert len(cleanup_scripts) == 1
    assert "pkill -f '[X]vnc :1 '" in cleanup_scripts[0]


def test_close_browser_failed_when_cleanup_times_out(
    tmp_path: Path,
) -> None:
    import subprocess as _sp

    mgr = _mgr(tmp_path)

    def run_side_effect(cmd: list[str], **kw: object) -> MagicMock:
        if cmd and cmd[0] == "/bin/sh":
            raise _sp.TimeoutExpired(cmd, 30.0)
        return MagicMock(returncode=0, stdout="stopped\n", stderr="")

    with patch("sellerclaw_agent.cloud.supervisor_manager.subprocess.run", side_effect=run_side_effect):
        outcome, err = mgr.close_browser()

    assert outcome == "failed"
    assert err is not None
