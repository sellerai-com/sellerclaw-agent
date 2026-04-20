from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TAG = "[openclaw_start]"


@dataclass(frozen=True)
class ConfigValidationResult:
    ok: bool
    errors: list[str]


def validate_gateway_config(config_path: Path) -> ConfigValidationResult:
    """Validate that openclaw.json exists and has token-mode auth configured.

    Returns a result with ok=True if the config is valid, or ok=False with
    human-readable error messages explaining what is wrong.  This runs before
    the gateway process starts to avoid silent fallback to pairing mode.
    """
    errors: list[str] = []

    if not config_path.exists():
        return ConfigValidationResult(ok=False, errors=[f"Config file does not exist: {config_path}"])

    raw = config_path.read_text(encoding="utf-8").strip()
    if not raw:
        return ConfigValidationResult(ok=False, errors=[f"Config file is empty: {config_path}"])

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return ConfigValidationResult(ok=False, errors=[f"Config file is not valid JSON: {exc}"])

    if not isinstance(payload, dict):
        return ConfigValidationResult(ok=False, errors=["Config root must be a JSON object"])

    gateway = payload.get("gateway")
    if not isinstance(gateway, dict):
        errors.append("Missing or invalid 'gateway' section")
        return ConfigValidationResult(ok=False, errors=errors)

    gw_mode = gateway.get("mode")
    if gw_mode != "local":
        errors.append(
            f"gateway.mode must be 'local', got {gw_mode!r}. "
            "Without local mode the gateway refuses to start."
        )

    auth = gateway.get("auth")
    if not isinstance(auth, dict):
        errors.append("Missing or invalid 'gateway.auth' section")
        return ConfigValidationResult(ok=False, errors=errors)

    mode = auth.get("mode")
    if mode != "token":
        errors.append(
            f"gateway.auth.mode must be 'token', got {mode!r}. "
            "Without token auth the gateway falls back to pairing mode."
        )

    token = auth.get("token")
    if not isinstance(token, str) or not token.strip():
        errors.append("gateway.auth.token is missing or empty")

    if errors:
        return ConfigValidationResult(ok=False, errors=errors)
    return ConfigValidationResult(ok=True, errors=[])


def run_validate_config(config_path: Path) -> int:
    """CLI entry-point: validate config and print results. Returns 0 on success, 1 on failure."""
    result = validate_gateway_config(config_path)
    if result.ok:
        print(f"{TAG} Config validation passed: {config_path}")
        return 0
    for error in result.errors:
        print(f"{TAG} Config validation FAILED: {error}")
    return 1
