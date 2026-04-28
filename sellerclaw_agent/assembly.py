from __future__ import annotations

from dataclasses import dataclass, field

from sellerclaw_agent.models import ModelTier


@dataclass(frozen=True)
class AssembledAgentConfig:
    """Fully assembled configuration for one OpenClaw agent.

    Contains all text content needed to produce workspace files.
    The caller writes these into the workspace directory structure.
    """

    agent_id: str
    name: str
    model_tier: ModelTier
    is_entry_point: bool
    subagent_ids: list[str]
    tools_allow: list[str]
    tools_deny: list[str]
    agents_md: str
    memory_md: str
    soul_md: str | None = None
    user_md: str | None = None
    tools_md: str | None = None
    identity_md: str | None = None
    skills: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        agent_id = self.agent_id.strip()
        if not agent_id:
            raise ValueError("Assembled agent_id must not be empty.")

        name = self.name.strip()
        if not name:
            raise ValueError("Assembled agent name must not be empty.")

        agents_md = self.agents_md.strip()
        if not agents_md:
            raise ValueError("Assembled agents_md must not be empty.")

        memory_md = self.memory_md.strip()
        if not memory_md:
            raise ValueError("Assembled memory_md must not be empty.")

        for skill_name, skill_content in self.skills.items():
            if not str(skill_name).strip():
                raise ValueError("Skill names must not be empty.")
            if not str(skill_content).strip():
                raise ValueError("Skill content must not be empty.")

        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "agents_md", agents_md)
        object.__setattr__(self, "memory_md", memory_md)
