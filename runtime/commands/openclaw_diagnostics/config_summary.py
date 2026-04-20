from __future__ import annotations

import json
from pathlib import Path

TAG = "[openclaw_start]"


def summarize_config(config_path: Path) -> list[str]:
    """Return log lines for channels/plugins summary (empty if parse fails, matching bash exit 0)."""
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"{TAG} Failed to parse config summary: {exc}"]

    lines: list[str] = []

    channels = payload.get("channels")
    if isinstance(channels, dict):
        configured = ", ".join(sorted(channels)) or "(none)"
        lines.append(f"{TAG} Config summary: channels={configured}")
        for channel_id in sorted(channels):
            section = channels.get(channel_id)
            health_enabled = None
            if isinstance(section, dict):
                health_monitor = section.get("healthMonitor")
                if isinstance(health_monitor, dict):
                    health_enabled = health_monitor.get("enabled")
            lines.append(
                f"{TAG} Channel {channel_id}: healthMonitor.enabled={health_enabled!r}"
            )

    plugins = payload.get("plugins")
    if isinstance(plugins, dict):
        entries = plugins.get("entries")
        entry_ids = sorted(entries) if isinstance(entries, dict) else entries
        load = plugins.get("load")
        load_paths = load.get("paths") if isinstance(load, dict) else None
        lines.append(
            f"{TAG} Plugin summary: "
            f"allow={plugins.get('allow')!r} entries={entry_ids!r} load.paths={load_paths!r}"
        )

    return lines
