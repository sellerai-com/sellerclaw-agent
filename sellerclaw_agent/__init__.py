"""SellerClaw Agent: OpenClaw bundle builder and agent resources."""

from __future__ import annotations

import os

__all__ = ["__version__"]

# Single source of truth: a build-time / runtime environment variable populated
# by the surrounding shell (setup.sh, Makefile, CI). Dockerfile bakes it into
# the image as an ENV layer so the value survives across `docker run`.
# Fallback marker makes "unknown" sessions easy to spot on the server side.
__version__ = os.environ.get("SELLERCLAW_AGENT_VERSION") or "0.0.0+unknown"
