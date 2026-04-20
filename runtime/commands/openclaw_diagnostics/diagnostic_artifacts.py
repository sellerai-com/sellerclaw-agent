from __future__ import annotations

from pathlib import Path

TAG = "[openclaw_start]"


def list_diagnostic_artifact_lines(diagnostic_dir: Path) -> list[str]:
    if not diagnostic_dir.is_dir():
        return [f"{TAG} Node diagnostic directory missing: {diagnostic_dir}"]

    files = sorted(p for p in diagnostic_dir.iterdir() if p.is_file())
    if not files:
        return [f"{TAG} Node diagnostic artifacts: none in {diagnostic_dir}"]

    lines = []
    for item in files:
        lines.append(
            f"{TAG} Node diagnostic artifact: path={item} size_bytes={item.stat().st_size}"
        )
    return lines
