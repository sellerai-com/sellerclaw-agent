from __future__ import annotations

import os
from pathlib import Path


def get_agent_resources_root() -> Path:
    """Directory with agent markdown templates (``agent_resources``).

    In the combined runtime image this is ``/app/agent_resources`` (see ``runtime/Dockerfile``).
    Override with ``AGENT_RESOURCES_ROOT``.
    """
    raw = os.environ.get("AGENT_RESOURCES_ROOT")
    if raw:
        return Path(raw)
    # sellerclaw_agent/paths.py -> parent = sellerclaw_agent, parent.parent = repo or /app
    return Path(__file__).resolve().parent.parent / "agent_resources"
