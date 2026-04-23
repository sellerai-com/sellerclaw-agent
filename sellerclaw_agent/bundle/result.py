from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BundleResult:
    """Output of BundleBuilder.build(): OpenClaw JSON + workspace files + content hash.

    ``shared_skills`` holds machine-wide skills destined for OpenClaw's managed
    skills directory (``~/.openclaw/skills``) — loaded once per machine and
    visible to every agent, rather than duplicated into each agent's workspace.
    """

    openclaw_config: str
    workspaces: dict[str, str]
    shared_skills: dict[str, str]
    version: str
    created_at: datetime
