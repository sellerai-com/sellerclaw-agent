from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BundleResult:
    """Output of BundleBuilder.build(): OpenClaw JSON + workspace files + content hash."""

    openclaw_config: str
    workspaces: dict[str, str]
    version: str
    created_at: datetime
