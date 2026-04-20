from __future__ import annotations

import urllib.error
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from openclaw_diagnostics.probe import (
    is_ready_payload,
    monitor_readiness,
    probe_endpoint_result,
    probe_readiness,
)

pytestmark = pytest.mark.unit


def test_is_ready_payload_true() -> None:
    assert is_ready_payload('{"ready": true}') is True


def test_is_ready_payload_false() -> None:
    assert is_ready_payload('{"ready": false}') is False


def test_is_ready_payload_invalid() -> None:
    assert is_ready_payload("not json") is False


def test_probe_endpoint_result_success() -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ready":true}'
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("openclaw_diagnostics.probe.urllib.request.urlopen", return_value=mock_resp):
        status, body = probe_endpoint_result("http://example.com/x")
    assert status == "200"
    assert "ready" in body


def test_probe_endpoint_result_http_error() -> None:
    err = urllib.error.HTTPError(
        "http://example.com/x",
        503,
        "Service Unavailable",
        Message(),
        BytesIO(b"bad"),
    )

    with patch("openclaw_diagnostics.probe.urllib.request.urlopen", side_effect=err):
        status, body = probe_endpoint_result("http://example.com/x")
    assert status == "503"
    assert body == "bad"


def test_probe_endpoint_result_connection_refused() -> None:
    with patch(
        "openclaw_diagnostics.probe.urllib.request.urlopen",
        side_effect=ConnectionRefusedError(111, "Connection refused"),
    ):
        status, body = probe_endpoint_result("http://127.0.0.1:9")
    assert status == "ERR"
    assert "111" in body or "refused" in body.lower()


def test_probe_readiness_prints(capsys: pytest.CaptureFixture[str]) -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"{}"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("openclaw_diagnostics.probe.urllib.request.urlopen", return_value=mock_resp):
        probe_readiness()
    out = capsys.readouterr().out
    assert "200" in out
    assert "\t" in out


def test_monitor_readiness_returns_when_pid_gone_before_probe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("openclaw_diagnostics.probe.os.kill", side_effect=ProcessLookupError()):
        monitor_readiness(99999, attempts=3, interval_seconds=0)
    assert capsys.readouterr().out == ""


def test_monitor_readiness_success_after_retry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("openclaw_diagnostics.probe.os.kill"),
        patch("openclaw_diagnostics.probe.time.sleep") as sleep_mock,
        patch(
            "openclaw_diagnostics.probe.probe_endpoint_result",
            side_effect=[
                ("200", '{"ready": false}'),
                ("200", '{"ready": true}'),
            ],
        ),
    ):
        monitor_readiness(1, attempts=5, interval_seconds=0)
    sleep_mock.assert_called_once_with(1)
    out = capsys.readouterr().out
    assert "attempt 1/5" in out
    assert "attempt 2/5" in out
    assert "ready=true on attempt 2/5" in out


def test_monitor_readiness_fallback_readyz_when_exhausted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("openclaw_diagnostics.probe.os.kill"),
        patch("openclaw_diagnostics.probe.time.sleep"),
        patch(
            "openclaw_diagnostics.probe.probe_endpoint_result",
            side_effect=[
                ("200", '{"ready": false}'),
                ("200", '{"ready": false}'),
                ("502", "bad gateway"),
            ],
        ),
    ):
        monitor_readiness(1, attempts=2, interval_seconds=0)
    out = capsys.readouterr().out
    assert "still not ready after 2 attempts" in out
    assert "readyz status=502" in out
    assert "body=bad gateway" in out
