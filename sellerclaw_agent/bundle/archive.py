from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sellerclaw_agent.bundle.protocols import AssembledAgentLike


def build_workspaces_from_assembled(assembled: Sequence[AssembledAgentLike]) -> dict[str, str]:
    """Convert assembled agent configs to workspace file mapping."""
    workspaces: dict[str, str] = {}
    for agent in assembled:
        prefix = agent.agent_id
        workspaces[f"{prefix}/AGENTS.md"] = agent.agents_md
        workspaces[f"{prefix}/MEMORY.md"] = agent.memory_md
        if agent.soul_md is not None:
            workspaces[f"{prefix}/SOUL.md"] = agent.soul_md
        if agent.user_md is not None:
            workspaces[f"{prefix}/USER.md"] = agent.user_md
        for skill_name, content in sorted(agent.skills.items()):
            workspaces[f"{prefix}/skills/{skill_name}/SKILL.md"] = content
    return workspaces


def build_gateway_version(*, openclaw_config: str, workspaces: dict[str, str]) -> str:
    payload = {
        "openclaw_config": openclaw_config,
        "workspaces": {key: workspaces[key] for key in sorted(workspaces.keys())},
    }
    content = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _add_tar_bytes(archive: tarfile.TarFile, arcname: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(payload)
    archive.addfile(tarinfo=info, fileobj=io.BytesIO(payload))


@dataclass(frozen=True)
class GatewayArchivePayload:
    """Minimal shape for building a gateway tar.gz (matches monolith GatewayBundle fields)."""

    openclaw_config: str
    workspaces: dict[str, str]
    created_at: datetime


def build_gateway_archive(payload: GatewayArchivePayload) -> bytes:
    """Pack OpenClaw JSON and workspace files into a gzip-compressed tar archive."""
    buffer = io.BytesIO()
    mtime = int(payload.created_at.timestamp())
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=mtime) as gz_stream:
        with tarfile.open(fileobj=gz_stream, mode="w") as archive:
            _add_tar_bytes(
                archive,
                "openclaw/openclaw.json",
                payload.openclaw_config.encode("utf-8"),
            )
            for relative_path, content in sorted(payload.workspaces.items()):
                archive_path = f"workspaces/{relative_path.lstrip('/')}"
                _add_tar_bytes(archive, archive_path, content.encode("utf-8"))
    return buffer.getvalue()
