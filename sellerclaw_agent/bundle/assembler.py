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
        variables = {**variables, "agent_id": "supervisor"}
        has_any_store = self._has_integration(
            enabled_modules=enabled_modules,
            kind=IntegrationKind.SHOPIFY_STORE,
        ) or self._has_integration(
            enabled_modules=enabled_modules,
            kind=IntegrationKind.EBAY_STORE,
        )
        has_supplier = self._has_integration(
            enabled_modules=enabled_modules,
            kind=IntegrationKind.SUPPLIER_CJ,
        ) or self._has_integration(
            enabled_modules=enabled_modules,
            kind=IntegrationKind.SUPPLIER_ANY,
        )

        sections: list[str] = [
            self._render(self._load_section("agents/supervisor/sections/core"), variables),
            self._render(
                self._load_section("agents/supervisor/sections/goal_tracking"),
                variables,
            ),
        ]

        if has_any_store:
            sections.append(
                self._render(
                    self._load_section("agents/supervisor/sections/store_management"), variables
                )
            )

        if has_supplier:
            sections.append(
                self._render(
                    self._load_section(
                        "agents/supervisor/sections/dropshipping_fulfillment",
                    ),
                    variables,
                )
            )

        agents_md = "\n\n".join(section for section in sections if section.strip())

        soul_md = self._render(self._load_section("souls/supervisor"), variables)

        user_md = self._render(self._load_section("agents/supervisor/user"), variables)

        base_supervisor_skills = ["file-storage", "owner-notifications", "goal-tracking"]
        supervisor_skill_names = self._deduplicate(
            [
                *base_supervisor_skills,
                *[module.supervisor_delegation_skill for module in enabled_modules],
                *[
                    skill_name
                    for module in enabled_modules
                    for skill_name in module.supervisor_skills
                ],
            ]
        )
        if enabled_modules:
            supervisor_skill_names = self._deduplicate(
                ["delegation-monitoring", *supervisor_skill_names]
            )
        if has_any_store or has_supplier:
            supervisor_skill_names = self._deduplicate(
                [*supervisor_skill_names, "domain-reference"]
            )
        skills = self._merge_supervisor_skills(
            supervisor_skill_names=supervisor_skill_names,
            variables=variables,
        )

        tools_allow = ["group:web", "web_search", "message", "browser", "cron", "exec"]
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

        module_variables = {**variables, "agent_id": module.agent_id}
        module_variables["capabilities_modes"] = capabilities_modes
        task_tracking_raw = (
            self.resources_root / "partials" / "task-tracking.md"
        ).read_text(encoding="utf-8")
        module_variables["task_tracking_section"] = self._render(
            task_tracking_raw,
            module_variables,
        )

        sections = [
            self._render(
                self._load_section(
                    f"agents/{module.agent_id}/sections/{section_path}",
                ),
                module_variables,
            )
            for section_path in module.agent_sections
        ]
        agents_md = "\n\n".join(section for section in sections if section.strip())

        all_skill_names = list(module.skills)
        for conditional_skill in module.conditional_skills:
            if conditional_skill.required_integration in connected_integrations:
                all_skill_names.append(conditional_skill.skill_name)

        skills = self._merge_module_skills(
            agent_id=module.agent_id,
            module_skill_names=all_skill_names,
            variables=module_variables,
        )
        soul_md = self._render(self._load_section("souls/subagent"), module_variables)

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
            user_md=None,
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

    def _shared_skills_root(self) -> Path:
        return self.resources_root / "shared-skills"

    def _module_skills_root(self) -> Path:
        return self.resources_root / "module-skills"

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

    def _load_all_shared_skills(self, variables: dict[str, str]) -> dict[str, str]:
        return {
            name: self._render(raw, variables)
            for name, raw in self._load_raw_shared_skills().items()
        }

    def assemble_shared_skills(self, variables: dict[str, str]) -> dict[str, str]:
        """Render shared skills (``agent_resources/shared-skills/``).

        Returned separately from per-agent skills so the caller can drop them
        into OpenClaw's machine-wide managed-skills directory
        (``~/.openclaw/skills``), visible to every agent without per-workspace
        duplication.
        """
        return self._load_all_shared_skills(variables)

    def _merge_supervisor_skills(
        self,
        *,
        supervisor_skill_names: list[str],
        variables: dict[str, str],
    ) -> dict[str, str]:
        shared_names = set(self._iter_shared_skill_names())
        skills: dict[str, str] = {}
        for name in supervisor_skill_names:
            agent_path = (
                self.resources_root / "agents" / "supervisor" / "skills" / name / "SKILL.md"
            )
            if agent_path.is_file():
                # Agent-specific override; workspace skills beat managed skills
                # in OpenClaw precedence, so this still wins over the shared copy.
                skills[name] = self._render(
                    self._load_skill_markdown(f"agents/supervisor/skills/{name}/SKILL"),
                    variables,
                )
            elif name in shared_names:
                # Loaded once globally from ~/.openclaw/skills; don't duplicate per-agent.
                continue
            else:
                raise FileNotFoundError(
                    f"Supervisor skill '{name}' not found: neither "
                    f"agents/supervisor/skills/{name}/SKILL.md nor "
                    f"shared-skills/{name}/SKILL.md exists."
                )
        return skills

    def _merge_module_skills(
        self,
        *,
        agent_id: str,
        module_skill_names: list[str],
        variables: dict[str, str],
    ) -> dict[str, str]:
        shared_names = set(self._iter_shared_skill_names())
        skills: dict[str, str] = {}
        for name in module_skill_names:
            agent_path = (
                self.resources_root / "agents" / agent_id / "skills" / name / "SKILL.md"
            )
            module_path = self._module_skills_root() / name / "SKILL.md"
            if agent_path.is_file():
                resolved = f"agents/{agent_id}/skills/{name}/SKILL"
            elif module_path.is_file():
                resolved = f"module-skills/{name}/SKILL"
            elif name in shared_names:
                continue
            else:
                raise FileNotFoundError(
                    f"Module skill '{name}' for agent '{agent_id}' not found: neither "
                    f"agents/{agent_id}/skills/{name}/SKILL.md nor "
                    f"module-skills/{name}/SKILL.md exists."
                )
            skills[name] = self._render(self._load_skill_markdown(resolved), variables)
        return skills

    def _load_skill_markdown(self, relative_path_without_md: str) -> str:
        """Load SKILL.md (path without .md suffix, must end with /SKILL)."""
        path = self.resources_root / f"{relative_path_without_md}.md"
        if not path.is_file():
            raise FileNotFoundError(
                f"Skill file not found: '{relative_path_without_md}.md' at '{path}'."
            )
        return path.read_text(encoding="utf-8")

    def _load_section(self, relative_path: str) -> str:
        """Load a markdown section by path relative to resources root."""
        path = self.resources_root / f"{relative_path}.md"
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(
                f"Resource section not found: '{relative_path}.md' at '{path}'."
            )
        return path.read_text(encoding="utf-8")

    def _has_integration(
        self,
        *,
        enabled_modules: list[AgentModuleDefinition],
        kind: IntegrationKind,
    ) -> bool:
        return any(
            requirement.kind == kind
            for module in enabled_modules
            for requirement in (
                *module.required_integrations,
                *module.recommended_integrations,
            )
        )

    def _deduplicate(self, values: list[str]) -> list[str]:
        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            unique_values.append(value)
        return unique_values
