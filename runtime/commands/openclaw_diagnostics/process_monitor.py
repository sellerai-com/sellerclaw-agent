from __future__ import annotations

import os
import time
from pathlib import Path

from openclaw_diagnostics.cgroup import cgroup_snapshot_raw_lines

TAG = "[openclaw_start]"
DEFAULT_PROC = Path("/proc")


def parse_kv(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def collect_child_tree(root_pid: str, *, proc_root: Path) -> list[str]:
    """Recursively collect all descendant PIDs starting from root_pid (inclusive)."""
    pids = [root_pid]
    ch_path = proc_root / root_pid / "task" / root_pid / "children"
    if ch_path.exists():
        try:
            for cpid in ch_path.read_text().split():
                pids.extend(collect_child_tree(cpid, proc_root=proc_root))
        except OSError:
            pass
    return pids


def emit_process_snapshot(pid: str, *, proc_root: Path = DEFAULT_PROC) -> None:
    """Print one snapshot of gateway process stats (and children, cgroup) to stdout."""
    status_path = proc_root / pid / "status"
    fd_path = proc_root / pid / "fd"
    smaps_rollup_path = proc_root / pid / "smaps_rollup"

    if not status_path.exists():
        return

    status = parse_kv(status_path.read_text(encoding="utf-8"))

    try:
        fd_count = len(list(fd_path.iterdir()))
    except OSError:
        fd_count = -1

    smaps_pss = "n/a"
    smaps_swap = "n/a"
    if smaps_rollup_path.exists():
        try:
            sm = parse_kv(smaps_rollup_path.read_text(encoding="utf-8"))
            smaps_pss = sm.get("Pss", "n/a")
            smaps_swap = sm.get("Swap", "n/a")
        except OSError:
            pass

    print(
        f"{TAG} Gateway process stats: "
        f"pid={pid} "
        f"rss={status.get('VmRSS', 'n/a')} "
        f"hwm={status.get('VmHWM', 'n/a')} "
        f"vm_size={status.get('VmSize', 'n/a')} "
        f"pss={smaps_pss} "
        f"swap={smaps_swap} "
        f"threads={status.get('Threads', 'n/a')} "
        f"fds={fd_count}"
    )

    children_path = proc_root / pid / "task" / pid / "children"
    child_pids: list[str] = []
    if children_path.exists():
        child_pids = children_path.read_text().split()

    all_descendant_pids: list[str] = []
    for cpid in child_pids:
        all_descendant_pids.extend(collect_child_tree(cpid, proc_root=proc_root))

    if all_descendant_pids:
        total_rss_kb = 0
        per_child: list[str] = []
        for cpid in all_descendant_pids:
            cs = proc_root / cpid / "status"
            if not cs.exists():
                continue
            cv = parse_kv(cs.read_text(encoding="utf-8"))
            name = cv.get("Name", "?")
            rss_raw = cv.get("VmRSS", "0 kB")
            try:
                rss_kb_val = int(rss_raw.split()[0]) if rss_raw != "n/a" else 0
            except (IndexError, ValueError):
                rss_kb_val = 0
            total_rss_kb += rss_kb_val
            per_child.append(f"{cpid}:{name}:{rss_raw}")
        print(
            f"{TAG} Gateway child processes: "
            f"count={len(all_descendant_pids)} "
            f"total_rss={total_rss_kb} kB "
            f"top=[{', '.join(per_child[:5])}]"
        )

    snap = cgroup_snapshot_raw_lines()
    if snap:
        print(snap)


def monitor_memory(
    openclaw_pid: int,
    *,
    interval_seconds: int,
    max_samples: int | None = None,
    proc_root: Path = DEFAULT_PROC,
) -> None:
    """Periodic memory logging until PID no longer exists or max_samples is reached."""
    pid_str = str(openclaw_pid)
    samples = 0
    while True:
        try:
            os.kill(openclaw_pid, 0)
        except OSError:
            return
        emit_process_snapshot(pid_str, proc_root=proc_root)
        samples += 1
        if max_samples is not None and samples >= max_samples:
            return
        time.sleep(interval_seconds)
