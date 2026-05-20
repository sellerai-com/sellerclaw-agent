from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BundleResult:
    """Output of BundleBuilder.build(): OpenClaw JSON + workspace files + content hash.

    ``shared_skills`` is kept for a stable field shape; per-agent skill content is
    embedded under each workspace's ``skills/``. The value is currently always empty.
    """

    openclaw_config: str
    workspaces: dict[str, str]
    shared_skills: dict[str, str]
    version: str
    created_at: datetime
