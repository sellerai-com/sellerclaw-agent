from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from sellerclaw_agent.assembly import AssembledAgentConfig
from sellerclaw_agent.models import (
    AgentModuleDefinition,
    AgentModuleId,
    IntegrationKind,
    ModelTier,
    resolve_capability_mode,
)
from sellerclaw_agent.registry import get_module_capability_definitions

_PARTIAL_REF_PATTERN = re.compile(r"\{\{([a-z][a-z0-9-]*)\}\}")


def _without_browser_tool(tools: list[str]) -> list[str]:
    return [tool for tool in tools if tool != "browser"]


@dataclass
class AgentConfigAssembler:
    """Assembles OpenClaw agent configurations from file resources and enabled modules.

    Pure function: reads files from disk, takes configuration inputs,
    produces AssembledAgentConfig outputs. No DB access.
    """

    resources_root: Path
    _raw_shared_skills_cache: dict[str, str] | None = field(default=None, repr=False)

    def assemble(
        self,
        *,
        enabled_modules: list[AgentModuleDefinition],
        template_variables: dict[str, str],
        connected_integrations: frozenset[IntegrationKind] = frozenset(),
        global_browser_enabled: bool = True,
        per_module_browser: dict[AgentModuleId, bool] | None = None,
    ) -> list[AssembledAgentConfig]:
        """Assemble complete agent configs for supervisor + all enabled modules."""
        browser_by_module = per_module_browser or {}
        supervisor = self._assemble_supervisor(
            enabled_modules=enabled_modules,
            variables=template_variables,
            global_browser_enabled=global_browser_enabled,
        )
        modules = [
            self._assemble_module(
                module=module,
                variables=template_variables,
                connected_integrations=connected_integrations,
                module_browser_enabled=browser_by_module.get(module.id, global_browser_enabled),
            )
            for module in enabled_modules
        ]
        return [supervisor, *modules]

    def assemble_supervisor_only(
        self,
        *,
        template_variables: dict[str, str],
        global_browser_enabled: bool = True,
    ) -> AssembledAgentConfig:
        """Assemble supervisor config with no subagents (convenience shortcut)."""
        return self.assemble(
            enabled_modules=[],
            template_variables=template_variables,
            global_browser_enabled=global_browser_enabled,
        )[0]

    def _assemble_supervisor(
        self,
        *,
        enabled_modules: list[AgentModuleDefinition],
        variables: dict[str, str],
        global_browser_enabled: bool,
    ) -> AssembledAgentConfig:
        variables = self._template_variables_with_partials(
            {**variables, "agent_id": "supervisor"},
        )

        agents_md = self._resolve_agents_md(
            agent_id="supervisor",
            variables=variables,
        )
        soul_md = self._resolve_optional_template(
            agent_id="supervisor",
            filename="soul.md",
            variables=variables,
        )
        user_md = self._resolve_optional_template(
            agent_id="supervisor",
            filename="user.md",
            variables=variables,
        )
        tools_md = self._resolve_optional_template(
            agent_id="supervisor",
            filename="tools.md",
            variables=variables,
        )
        identity_md = self._resolve_optional_template(
            agent_id="supervisor",
            filename="identity.md",
            variables=variables,
        )
        heartbeat_md = self._resolve_optional_template(
            agent_id="supervisor",
            filename="heartbeat.md",
            variables=variables,
        )

        skills = self._build_agent_skills(
            agent_id="supervisor",
            variables=variables,
            remove_skill_names=frozenset(),
        )

        tools_allow = ["group:web", "web_search", "message", "browser", "cron", "exec", "image_generate"]
        if enabled_modules:
            tools_allow = ["group:sessions", *tools_allow]
        else:
            tools_allow.extend(["group:fs", "process"])
        if not global_browser_enabled:
            tools_allow = _without_browser_tool(tools_allow)

        return AssembledAgentConfig(
            agent_id="supervisor",
            name="Supervisor",
            model_tier=ModelTier.COMPLEX,
            is_entry_point=True,
            subagent_ids=[module.agent_id for module in enabled_modules],
            tools_allow=tools_allow,
            tools_deny=[],
            agents_md=agents_md,
            memory_md="# Agent memory: supervisor\n",
            soul_md=soul_md,
            user_md=user_md,
            tools_md=tools_md,
            identity_md=identity_md,
            heartbeat_md=heartbeat_md,
            skills=skills,
        )

    def _assemble_module(
        self,
        *,
        module: AgentModuleDefinition,
        variables: dict[str, str],
        connected_integrations: frozenset[IntegrationKind],
        module_browser_enabled: bool,
    ) -> AssembledAgentConfig:
        capabilities_modes = self._render_capabilities_modes(
            module_id=module.id,
            connected_integrations=connected_integrations,
            browser_enabled=module_browser_enabled,
        )

        module_variables = self._template_variables_with_partials(
            {
                **variables,
                "agent_id": module.agent_id,
                "capabilities_modes": capabilities_modes,
            },
        )

        agents_md = self._resolve_agents_md(
            agent_id=module.agent_id,
            variables=module_variables,
        )

        remove_names = frozenset(
            cond.skill_name
            for cond in module.conditional_skills
            if cond.required_integration not in connected_integrations
        )
        skills = self._build_agent_skills(
            agent_id=module.agent_id,
            variables=module_variables,
            remove_skill_names=remove_names,
        )
        soul_md = self._resolve_optional_template(
            agent_id=module.agent_id,
            filename="soul.md",
            variables=module_variables,
        )
        user_md = self._resolve_optional_template(
            agent_id=module.agent_id,
            filename="user.md",
            variables=module_variables,
        )
        tools_md = self._resolve_optional_template(
            agent_id=module.agent_id,
            filename="tools.md",
            variables=module_variables,
        )
        identity_md = self._resolve_optional_template(
            agent_id=module.agent_id,
            filename="identity.md",
            variables=module_variables,
        )

        mod_tools = list(module.tools_allow)
        if not module_browser_enabled:
            mod_tools = _without_browser_tool(mod_tools)

        return AssembledAgentConfig(
            agent_id=module.agent_id,
            name=module.name,
            model_tier=module.model_tier,
            is_entry_point=False,
            subagent_ids=[],
            tools_allow=mod_tools,
            tools_deny=list(module.tools_deny),
            agents_md=agents_md,
            memory_md=f"# Agent memory: {module.agent_id}\n",
            soul_md=soul_md,
            user_md=user_md,
            tools_md=tools_md,
            identity_md=identity_md,
            skills=skills,
        )

    def _render_capabilities_modes(
        self,
        *,
        module_id: AgentModuleId,
        connected_integrations: frozenset[IntegrationKind],
        browser_enabled: bool,
    ) -> str:
        """Render per-capability operating modes as a markdown list."""
        capabilities = get_module_capability_definitions(module_id)
        if not capabilities:
            return "No capabilities defined."

        lines: list[str] = []
        for capability in capabilities:
            mode = resolve_capability_mode(
                capability, connected_integrations, browser_enabled
            )
            lines.append(f"- **{capability.name}** — {mode.value}: {capability.description}")
        return "\n".join(lines)

    def _render(self, template: str, variables: dict[str, str]) -> str:
        """Render a template by resolving partial references then variables."""
        rendered = self._resolve_partials(template, variables)
        for key, value in variables.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", value)
        return rendered

    def _resolve_partials(self, template: str, variables: dict[str, str]) -> str:
        """Replace {{partial-name}} tokens with rendered partial file content."""

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            partial_path = self.resources_root / "partials" / f"{name}.md"
            if not partial_path.exists():
                return match.group(0)

            content = partial_path.read_text(encoding="utf-8")
            for key, value in variables.items():
                content = content.replace(f"{{{{{key}}}}}", value)
            return content

        return _PARTIAL_REF_PATTERN.sub(_replace, template)

    def _template_variables_with_partials(self, base: dict[str, str]) -> dict[str, str]:
        """Merge ``partials/<name>.md`` into the template context.

        Each file stem (e.g. ``common-tools``, ``subagent-execution-rules``) becomes
        a key whose value is that file after :meth:`_render` (so nested ``{{...}}`` in
        partials resolve the same as elsewhere). Keys from ``base`` (manifest, derived
        fields) override the same-named partial on collision.
        """
        partial_dir = self.resources_root / "partials"
        if not partial_dir.is_dir():
            return dict(base)
        rendered: dict[str, str] = {}
        for path in sorted(partial_dir.glob("*.md")):
            stem = path.stem
            raw = path.read_text(encoding="utf-8")
            merged: dict[str, str] = {**base, **rendered}
            rendered[stem] = self._render(raw, merged)
        return {**rendered, **base}

    def _shared_skills_root(self) -> Path:
        return self.resources_root / "shared-skills"

    def _iter_shared_skill_names(self) -> list[str]:
        root = self._shared_skills_root()
        if not root.is_dir():
            return []
        names: list[str] = []
        for skill_dir in sorted(root.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                names.append(skill_dir.name)
        return names

    def _load_raw_shared_skills(self) -> dict[str, str]:
        if self._raw_shared_skills_cache is not None:
            return self._raw_shared_skills_cache
        result: dict[str, str] = {}
        for name in self._iter_shared_skill_names():
            result[name] = self._load_skill_markdown(f"shared-skills/{name}/SKILL")
        self._raw_shared_skills_cache = result
        return result

    def assemble_shared_skills(self, _variables: dict[str, str]) -> dict[str, str]:
        """Shared skill markdown is embedded in each agent workspace (see ``_build_agent_skills``).

        This hook remains empty: the bundle no longer ships a separate machine-wide
        ``shared-skills/`` copy to avoid duplicating the same content twice.
        """
        return {}

    def _iter_agent_skill_dir_names(self, agent_id: str) -> list[str]:
        root = self.resources_root / "agents" / agent_id / "skills"
        if not root.is_dir():
            return []
        return sorted(
            p.name
            for p in root.iterdir()
            if p.is_dir() and (p / "SKILL.md").is_file()
        )

    def _build_agent_skills(
        self,
        *,
        agent_id: str,
        variables: dict[str, str],
        remove_skill_names: frozenset[str] = frozenset(),
    ) -> dict[str, str]:
        """Merge shared skills with ``agents/<agent_id>/skills/*/SKILL.md`` (agent wins on name clash).

        Names in ``remove_skill_names`` are dropped after merge (conditional integrations).
        """
        skills: dict[str, str] = {}
        for name, raw in self._load_raw_shared_skills().items():
            skills[name] = self._render(raw, variables)
        for name in self._iter_agent_skill_dir_names(agent_id):
            rel = f"agents/{agent_id}/skills/{name}/SKILL"
            skills[name] = self._render(self._load_skill_markdown(rel), variables)
        for name in remove_skill_names:
            skills.pop(name, None)
        if not skills:
            raise FileNotFoundError(
                f"No skills assembled for agent '{agent_id}': add shared skills under "
                f"'shared-skills/' and/or 'agents/{agent_id}/skills/<name>/SKILL.md'."
            )
        return skills

    def _load_skill_markdown(self, relative_path_without_md: str) -> str:
        """Load SKILL.md (path without .md suffix, must end with /SKILL)."""
        path = self.resources_root / f"{relative_path_without_md}.md"
        if not path.is_file():
            raise FileNotFoundError(
                f"Skill file not found: '{relative_path_without_md}.md' at '{path}'."
            )
        return path.read_text(encoding="utf-8")

    def _resolve_agents_md(
        self,
        *,
        agent_id: str,
        variables: dict[str, str],
    ) -> str:
        agents_path = self.resources_root / "agents" / agent_id / "agents.md"
        if not agents_path.is_file():
            raise FileNotFoundError(
                "Agent AGENTS.md template missing: expected "
                f"'agents/{agent_id}/agents.md' at '{agents_path}'."
            )
        return self._render(agents_path.read_text(encoding="utf-8"), variables)

    def _resolve_optional_template(
        self,
        *,
        agent_id: str,
        filename: str,
        variables: dict[str, str],
    ) -> str | None:
        path = self.resources_root / "agents" / agent_id / filename
        if not path.is_file():
            return None
        return self._render(path.read_text(encoding="utf-8"), variables)
