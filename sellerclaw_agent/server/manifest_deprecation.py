"""One-time-per-process warnings for deprecated ``POST /manifest`` fields."""

from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)
_WARNED: set[str] = set()


def reset_manifest_deprecation_warnings() -> None:
    """Clear warning state (for tests)."""
    _WARNED.clear()


def warn_deprecated_manifest_fields(*, gateway_token_set: bool, hooks_token_set: bool) -> None:
    if gateway_token_set and "gateway_token" not in _WARNED:
        _WARNED.add("gateway_token")
        _LOG.warning(
            "Ignoring deprecated manifest field 'gateway_token' (use local secrets store / SELLERCLAW_GATEWAY_TOKEN)"
        )
    if hooks_token_set and "hooks_token" not in _WARNED:
        _WARNED.add("hooks_token")
        _LOG.warning(
            "Ignoring deprecated manifest field 'hooks_token' (use local secrets store / SELLERCLAW_HOOKS_TOKEN)"
        )
