from __future__ import annotations

import json
from pathlib import Path

import pytest
from openclaw_diagnostics.config_validation import (
    run_validate_config,
    validate_gateway_config,
)

pytestmark = pytest.mark.unit


def _write_config(tmp_path: Path, payload: object) -> Path:
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(json.dumps(payload), encoding="utf-8")
    return cfg


class TestValidateGatewayConfig:
    def test_valid_token_config(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path, {
            "gateway": {
                "mode": "local",
                "auth": {"mode": "token", "token": "scw_gateway_abc123"},
                "trustedProxies": ["127.0.0.0/8"],
            },
        })
        result = validate_gateway_config(cfg)
        assert result.ok is True
        assert result.errors == []

    @pytest.mark.parametrize(
        ("payload", "expected_error_substr"),
        [
            pytest.param(
                {"gateway": {"auth": {"mode": "pairing"}}},
                "must be 'token'",
                id="pairing-mode",
            ),
            pytest.param(
                {"gateway": {"auth": {"mode": "password", "password": "s3cr3t"}}},
                "must be 'token'",
                id="password-mode",
            ),
            pytest.param(
                {"gateway": {"auth": {"mode": "token", "token": ""}}},
                "token is missing or empty",
                id="empty-token",
            ),
            pytest.param(
                {"gateway": {"auth": {"mode": "token"}}},
                "token is missing or empty",
                id="missing-token-key",
            ),
            pytest.param(
                {"gateway": {"auth": {"mode": "token", "token": "   "}}},
                "token is missing or empty",
                id="whitespace-only-token",
            ),
            pytest.param(
                {"gateway": {"auth": {}}},
                "must be 'token'",
                id="no-mode-key",
            ),
            pytest.param(
                {"gateway": {}},
                "Missing or invalid 'gateway.auth'",
                id="no-auth-section",
            ),
            pytest.param(
                {},
                "Missing or invalid 'gateway' section",
                id="no-gateway-section",
            ),
            pytest.param(
                {"gateway": {"auth": {"mode": "token", "token": "abc"}}},
                "must be 'local'",
                id="gateway-mode-missing",
            ),
            pytest.param(
                {"gateway": {"mode": "remote", "auth": {"mode": "token", "token": "abc"}}},
                "must be 'local'",
                id="gateway-mode-remote",
            ),
        ],
    )
    def test_invalid_configs(
        self,
        tmp_path: Path,
        payload: dict[str, object],
        expected_error_substr: str,
    ) -> None:
        cfg = _write_config(tmp_path, payload)
        result = validate_gateway_config(cfg)
        assert result.ok is False
        assert any(expected_error_substr in e for e in result.errors), (
            f"Expected substring {expected_error_substr!r} in errors: {result.errors}"
        )

    def test_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.json"
        result = validate_gateway_config(missing)
        assert result.ok is False
        assert any("does not exist" in e for e in result.errors)

    def test_empty_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "empty.json"
        cfg.write_text("", encoding="utf-8")
        result = validate_gateway_config(cfg)
        assert result.ok is False
        assert any("empty" in e for e in result.errors)

    def test_invalid_json(self, tmp_path: Path) -> None:
        cfg = tmp_path / "bad.json"
        cfg.write_text("{not json at all", encoding="utf-8")
        result = validate_gateway_config(cfg)
        assert result.ok is False
        assert any("not valid JSON" in e for e in result.errors)

    def test_root_is_not_object(self, tmp_path: Path) -> None:
        cfg = tmp_path / "array.json"
        cfg.write_text("[1,2,3]", encoding="utf-8")
        result = validate_gateway_config(cfg)
        assert result.ok is False
        assert any("JSON object" in e for e in result.errors)

    def test_multiple_errors_mode_and_token(self, tmp_path: Path) -> None:
        """When both mode and token are wrong, both errors are reported."""
        cfg = _write_config(tmp_path, {
            "gateway": {"mode": "local", "auth": {"mode": "pairing", "token": ""}},
        })
        result = validate_gateway_config(cfg)
        assert result.ok is False
        assert len(result.errors) == 2
        assert any("must be 'token'" in e for e in result.errors)
        assert any("token is missing or empty" in e for e in result.errors)


class TestRunValidateConfig:
    def test_success_exit_code(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path, {
            "gateway": {"mode": "local", "auth": {"mode": "token", "token": "scw_gateway_abc"}},
        })
        assert run_validate_config(cfg) == 0

    def test_failure_exit_code(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path, {"gateway": {}})
        assert run_validate_config(cfg) == 1

    def test_success_prints_passed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _write_config(tmp_path, {
            "gateway": {"mode": "local", "auth": {"mode": "token", "token": "scw_gateway_abc"}},
        })
        run_validate_config(cfg)
        assert "validation passed" in capsys.readouterr().out

    def test_failure_prints_errors(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _write_config(tmp_path, {})
        run_validate_config(cfg)
        assert "FAILED" in capsys.readouterr().out
