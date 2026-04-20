from __future__ import annotations

import json
from pathlib import Path

TAG = "[openclaw_start]"


def summarize_reports(diagnostic_dir: Path) -> list[str]:
    if not diagnostic_dir.is_dir():
        return []

    reports = sorted(diagnostic_dir.glob("*.json"))
    if not reports:
        return [f"{TAG} No Node.js diagnostic reports found"]

    lines: list[str] = []
    for rpath in reports:
        lines.extend(_lines_for_report(rpath))
    return lines


def _lines_for_report(rpath: Path) -> list[str]:
    lines: list[str] = []
    try:
        data = json.loads(rpath.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"{TAG} Report parse error: path={rpath} error={exc}"]

    header = data.get("header", {})
    js_stack = data.get("javascriptStack", {})
    res_usage = data.get("resourceUsage", {})
    js_heap = data.get("javascriptHeap", {})

    trigger = header.get("trigger", "unknown")
    event = header.get("event", "unknown")
    node_ver = header.get("nodejsVersion", "?")

    rss_bytes = res_usage.get("rss", 0)
    rss_mb = rss_bytes / (1024 * 1024) if rss_bytes else 0

    heap_total = js_heap.get("totalMemory", 0)
    heap_used = js_heap.get("usedMemory", 0)
    heap_limit = js_heap.get("memoryLimit", 0)
    heap_spaces = js_heap.get("spaces", [])

    lines.append(
        f"{TAG} Node report: path={rpath.name} "
        f"trigger={trigger} event={event} node={node_ver} "
        f"rss_mb={rss_mb:.0f} "
        f"heap_total_mb={heap_total / 1048576:.0f} "
        f"heap_used_mb={heap_used / 1048576:.0f} "
        f"heap_limit_mb={heap_limit / 1048576:.0f}"
    )

    if heap_spaces:
        for space in heap_spaces:
            name = space.get("spaceName", "?")
            used = space.get("spaceUsedSize", 0)
            avail = space.get("spaceAvailableSize", 0)
            cap = space.get("physicalSpaceSize", 0)
            pct = (used / cap * 100) if cap else 0
            if used > 10 * 1024 * 1024:
                lines.append(
                    f"{TAG}   heap space: {name} "
                    f"used={used / 1048576:.0f}MB "
                    f"avail={avail / 1048576:.0f}MB "
                    f"physical={cap / 1048576:.0f}MB "
                    f"usage={pct:.0f}%"
                )

    stack_msg = js_stack.get("message", "")
    stack_frames = js_stack.get("stack", [])
    if stack_msg:
        lines.append(f"{TAG}   JS error: {stack_msg}")
    if stack_frames:
        for frame in stack_frames[:5]:
            lines.append(f"{TAG}   at {frame}")
    return lines
