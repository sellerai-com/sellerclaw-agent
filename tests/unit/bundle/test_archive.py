from __future__ import annotations

import gzip
import io
import json
import tarfile
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.bundle.archive import (
    GatewayArchivePayload,
    build_gateway_archive,
    build_gateway_version,
    build_workspaces_from_assembled,
)

pytestmark = pytest.mark.unit


def test_build_gateway_archive_contains_paths() -> None:
    payload = GatewayArchivePayload(
        openclaw_config='{"x":1}',
        workspaces={"a/b.md": "c"},
        created_at=datetime.now(tz=UTC),
    )
    raw = build_gateway_archive(payload)
    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            names = tar.getnames()
    assert "openclaw/openclaw.json" in names
    assert "workspaces/a/b.md" in names


def test_build_gateway_archive_file_contents() -> None:
    cfg = '{"hello":"world"}'
    payload = GatewayArchivePayload(
        openclaw_config=cfg,
        workspaces={"supervisor/AGENTS.md": "# Title"},
        created_at=datetime.now(tz=UTC),
    )
    raw = build_gateway_archive(payload)
    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            jf = tar.extractfile("openclaw/openclaw.json")
            assert jf is not None
            assert json.loads(jf.read().decode("utf-8")) == {"hello": "world"}
            wf = tar.extractfile("workspaces/supervisor/AGENTS.md")
            assert wf is not None
            assert wf.read().decode("utf-8") == "# Title"


def test_build_gateway_version_is_deterministic_for_same_payload() -> None:
    ws = {"a/x.md": "1", "b/y.md": "2"}
    v1 = build_gateway_version(openclaw_config='{"k":1}', workspaces=ws)
    v2 = build_gateway_version(openclaw_config='{"k":1}', workspaces=ws)
    assert v1 == v2
    assert len(v1) == 64


def test_build_gateway_version_differs_when_workspace_changes() -> None:
    v1 = build_gateway_version(openclaw_config="{}", workspaces={"a.md": "x"})
    v2 = build_gateway_version(openclaw_config="{}", workspaces={"a.md": "y"})
    assert v1 != v2


def test_build_workspaces_from_assembled_maps_agents_memory_soul_user_and_skills(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    agent = make_assembled_agent(
        agent_id="lead",
        skills={"alpha": "# Alpha skill", "beta": "# Beta skill"},
    )
    ws = build_workspaces_from_assembled([agent])
    assert ws["lead/AGENTS.md"] == agent.agents_md
    assert ws["lead/MEMORY.md"] == agent.memory_md
    assert ws["lead/SOUL.md"] == agent.soul_md
    assert ws["lead/USER.md"] == agent.user_md
    assert ws["lead/skills/alpha/SKILL.md"] == "# Alpha skill"
    assert ws["lead/skills/beta/SKILL.md"] == "# Beta skill"


def test_build_workspaces_from_assembled_skips_missing_soul_user_and_empty_skills(
    make_assembled_agent: Callable[..., AssembledAgentConfig],
) -> None:
    agent = make_assembled_agent(
        soul_md=None,
        user_md=None,
        skills={},
    )
    ws = build_workspaces_from_assembled([agent])
    keys = set(ws.keys())
    assert f"{agent.agent_id}/SOUL.md" not in keys
    assert f"{agent.agent_id}/USER.md" not in keys
    assert not any("/skills/" in k for k in keys)


def test_build_gateway_archive_empty_workspaces() -> None:
    payload = GatewayArchivePayload(
        openclaw_config="{}",
        workspaces={},
        created_at=datetime.now(tz=UTC),
    )
    raw = build_gateway_archive(payload)
    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            names = tar.getnames()
    assert names == ["openclaw/openclaw.json"]


def test_build_gateway_archive_packs_shared_skills_at_top_level() -> None:
    """Shared skills live under ``shared-skills/`` in the tar (parallel to ``workspaces/``)."""
    payload = GatewayArchivePayload(
        openclaw_config="{}",
        workspaces={"supervisor/AGENTS.md": "# A"},
        created_at=datetime.now(tz=UTC),
        shared_skills={"file-storage": "# File Storage", "task-reporting": "# Task Reporting"},
    )
    raw = build_gateway_archive(payload)
    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            names = tar.getnames()
            fs = tar.extractfile("shared-skills/file-storage/SKILL.md")
            assert fs is not None
            assert fs.read().decode("utf-8") == "# File Storage"
    assert "shared-skills/file-storage/SKILL.md" in names
    assert "shared-skills/task-reporting/SKILL.md" in names


def test_build_gateway_version_includes_shared_skills() -> None:
    v_without = build_gateway_version(openclaw_config="{}", workspaces={"a.md": "x"})
    v_with = build_gateway_version(
        openclaw_config="{}",
        workspaces={"a.md": "x"},
        shared_skills={"foo": "bar"},
    )
    assert v_without != v_with
