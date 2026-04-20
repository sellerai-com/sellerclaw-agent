from __future__ import annotations

from pathlib import Path

TAG = "[openclaw_start]"

# Default host paths; overridable in tests via parameters.
DEFAULT_CGROUP_SYS = Path("/sys/fs/cgroup")


def read_val(path: Path) -> str | None:
    try:
        return path.read_text().strip() if path.exists() else None
    except OSError:
        return None


def fmt_bytes(raw: str | None) -> str:
    if raw is None or raw == "max":
        return raw or "n/a"
    try:
        b = int(raw)
        return f"{b / (1024 * 1024):.0f}MB"
    except ValueError:
        return raw


def cgroup_limits_lines(*, cgroup_sys: Path = DEFAULT_CGROUP_SYS) -> list[str]:
    """Lines printed for `cgroup-limits` at startup (cgroup v2 with v1 fallback)."""
    current = read_val(cgroup_sys / "memory.current")
    limit = read_val(cgroup_sys / "memory.max")
    swap = read_val(cgroup_sys / "memory.swap.current")
    swap_max = read_val(cgroup_sys / "memory.swap.max")

    if current is None:
        mem = cgroup_sys / "memory"
        current = read_val(mem / "memory.usage_in_bytes")
        limit = read_val(mem / "memory.limit_in_bytes")
        swap = read_val(mem / "memory.memsw.usage_in_bytes")
        swap_max = read_val(mem / "memory.memsw.limit_in_bytes")

    if current is not None:
        return [
            f"{TAG} Cgroup memory at startup: "
            f"current={fmt_bytes(current)} limit={fmt_bytes(limit)} "
            f"swap={fmt_bytes(swap)} swap_max={fmt_bytes(swap_max)}"
        ]
    return [f"{TAG} Cgroup memory info not available"]


def cgroup_snapshot_raw_lines(*, cgroup_sys: Path = DEFAULT_CGROUP_SYS) -> str | None:
    """Single line for periodic process monitor: raw k=v cgroup memory files."""
    cgroup_paths = [
        cgroup_sys / "memory.current",
        cgroup_sys / "memory.max",
        cgroup_sys / "memory" / "memory.usage_in_bytes",
        cgroup_sys / "memory" / "memory.limit_in_bytes",
    ]
    cg: dict[str, str] = {}
    for cp in cgroup_paths:
        if cp.exists():
            val = read_val(cp)
            if val is not None:
                cg[cp.name] = val
    if not cg:
        return None
    parts = " ".join(f"{k}={v}" for k, v in sorted(cg.items()))
    return f"{TAG} Cgroup memory: {parts}"
