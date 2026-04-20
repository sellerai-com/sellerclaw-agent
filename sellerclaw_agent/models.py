from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class ModelTier(StrEnum):
    """Model tier for agent workloads (maps to complex vs simple LiteLLM groups)."""

    COMPLEX = "complex"
    SIMPLE = "simple"


def _normalize_items(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize and strip string items; raise ValueError for empty values."""
    normalized: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value:
            raise ValueError("Values must not be empty.")
        normalized.append(value)
    return tuple(normalized)


def _ensure_unique(values: Iterable[str], *, label: str) -> None:
    """Ensure no duplicate values; raise ValueError if duplicates found."""
    items = list(values)
    if len(set(items)) != len(items):
        raise ValueError(f"Duplicate {label} values are not allowed.")


class AgentModuleId(StrEnum):
    """Unique identifiers of product-defined agent modules."""

    SHOPIFY_STORE_MANAGER = "shopify_store_manager"
    EBAY_STORE_MANAGER = "ebay_store_manager"
    DROPSHIPPING_SUPPLIER = "dropshipping_supplier"
    MARKETING_MANAGER = "marketing_manager"
    PRODUCT_SCOUT = "product_scout"


class IntegrationKind(StrEnum):
    """Integration kinds required by modules before enabling."""

    EBAY_STORE = "ebay_store"
    SHOPIFY_STORE = "shopify_store"
    SHOPIFY_THEMES = "shopify_themes"
    SUPPLIER_CJ = "supplier_cj"
    SUPPLIER_ANY = "supplier_any"
    FACEBOOK_ADS = "facebook_ads"
    GOOGLE_ADS = "google_ads"
    RESEARCH_TRENDS = "research_trends"
    RESEARCH_SEO = "research_seo"
    RESEARCH_SOCIAL = "research_social"


INTEGRATION_DISPLAY_NAMES: dict[IntegrationKind, str] = {
    IntegrationKind.SHOPIFY_STORE: "Shopify Stores",
    IntegrationKind.SHOPIFY_THEMES: "Shopify Theme API",
    IntegrationKind.EBAY_STORE: "eBay Stores",
    IntegrationKind.SUPPLIER_CJ: "CJ Dropshipping",
    IntegrationKind.SUPPLIER_ANY: "Supplier platforms",
    IntegrationKind.FACEBOOK_ADS: "Facebook Ads",
    IntegrationKind.GOOGLE_ADS: "Google Ads",
    IntegrationKind.RESEARCH_TRENDS: "Google Trends (research)",
    IntegrationKind.RESEARCH_SEO: "DataForSEO (keyword & market research)",
    IntegrationKind.RESEARCH_SOCIAL: "SociaVault (social & TikTok Shop research)",
}


class OperatingMode(StrEnum):
    """Operating mode for module capabilities."""

    AUTONOMOUS = "autonomous"
    ASSISTED = "assisted"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class CapabilityLevelDefinition:
    """Static definition of one capability level."""

    mode: OperatingMode
    description: str
    enabled_by: str | None
    required_integrations: tuple[IntegrationKind, ...] = ()
    requires_browser: bool = False
    require_all_integrations: bool = False
    require_one_of: tuple[IntegrationKind, ...] = ()

    def __post_init__(self) -> None:
        if self.require_all_integrations and not self.required_integrations:
            raise ValueError(
                "require_all_integrations=True requires at least one required_integrations entry."
            )
        if self.require_one_of:
            if not self.require_all_integrations:
                raise ValueError("require_one_of is only supported with require_all_integrations=True.")
            if not self.required_integrations:
                raise ValueError("require_one_of requires at least one entry in required_integrations.")


@dataclass(frozen=True)
class ModuleCapabilityDefinition:
    """Static definition of one module capability."""

    id: str
    name: str
    description: str
    levels: tuple[CapabilityLevelDefinition, ...]

    def __post_init__(self) -> None:
        capability_id = self.id.strip()
        if not capability_id:
            raise ValueError("Capability id must not be empty.")

        capability_name = self.name.strip()
        if not capability_name:
            raise ValueError("Capability name must not be empty.")

        capability_description = self.description.strip()
        if not capability_description:
            raise ValueError("Capability description must not be empty.")

        levels = tuple(self.levels)
        if not levels:
            raise ValueError("Capability levels must not be empty.")

        modes = [level.mode for level in levels]
        if len(set(modes)) != len(modes):
            raise ValueError("Capability levels must not contain duplicate modes.")

        mode_rank = {
            OperatingMode.AUTONOMOUS: 3,
            OperatingMode.ASSISTED: 2,
            OperatingMode.ADVISORY: 1,
        }
        expected = sorted(modes, key=lambda mode: mode_rank[mode], reverse=True)
        if modes != expected:
            raise ValueError(
                "Capability levels must be ordered by priority: "
                "autonomous -> assisted -> advisory."
            )

        object.__setattr__(self, "id", capability_id)
        object.__setattr__(self, "name", capability_name)
        object.__setattr__(self, "description", capability_description)
        object.__setattr__(self, "levels", levels)


def is_level_active(
    level: CapabilityLevelDefinition,
    connected_integrations: frozenset[IntegrationKind],
    browser_active: bool,
) -> bool:
    """Compute whether a capability level is active."""
    if level.required_integrations:
        if level.require_all_integrations:
            all_required = all(kind in connected_integrations for kind in level.required_integrations)
            if level.require_one_of:
                any_alt = any(kind in connected_integrations for kind in level.require_one_of)
                return all_required and any_alt
            return all_required
        return any(kind in connected_integrations for kind in level.required_integrations)
    if level.requires_browser:
        return browser_active
    return True


def resolve_capability_mode(
    capability: ModuleCapabilityDefinition,
    connected_integrations: frozenset[IntegrationKind],
    browser_active: bool,
) -> OperatingMode:
    """Return the most powerful active mode for a capability."""
    for level in capability.levels:
        if is_level_active(level, connected_integrations, browser_active):
            return level.mode
    return OperatingMode.ADVISORY


@dataclass(frozen=True)
class IntegrationRequirement:
    """Defines one required integration for a module."""

    kind: IntegrationKind
    description: str

    def __post_init__(self) -> None:
        description = self.description.strip()
        if not description:
            raise ValueError("Integration requirement description must not be empty.")
        object.__setattr__(self, "description", description)


@dataclass(frozen=True)
class ConditionalSkill:
    """A skill that is included only when a specific integration is connected."""

    skill_name: str
    required_integration: IntegrationKind

    def __post_init__(self) -> None:
        skill_name = self.skill_name.strip()
        if not skill_name:
            raise ValueError("Conditional skill name must not be empty.")
        object.__setattr__(self, "skill_name", skill_name)


@dataclass(frozen=True)
class AgentModuleDefinition:
    """Static product definition of an available module."""

    id: AgentModuleId
    name: str
    description: str
    agent_id: str
    model_tier: ModelTier
    required_integrations: tuple[IntegrationRequirement, ...]
    tools_allow: tuple[str, ...]
    tools_deny: tuple[str, ...]
    skills: tuple[str, ...]
    agent_sections: tuple[str, ...]
    supervisor_delegation_skill: str
    supervisor_skills: tuple[str, ...]
    recommended_integrations: tuple[IntegrationRequirement, ...] = ()
    conditional_skills: tuple[ConditionalSkill, ...] = ()

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("Module name must not be empty.")

        description = self.description.strip()
        if not description:
            raise ValueError("Module description must not be empty.")

        agent_id = self.agent_id.strip()
        if not agent_id:
            raise ValueError("Module agent_id must not be empty.")

        if not isinstance(self.model_tier, ModelTier):
            raise ValueError("Module model_tier must be a ModelTier enum value.")

        skills = _normalize_items(self.skills)
        agent_sections = _normalize_items(self.agent_sections)
        tools_allow = _normalize_items(self.tools_allow)
        tools_deny = _normalize_items(self.tools_deny)
        supervisor_skills = _normalize_items(self.supervisor_skills)

        supervisor_delegation_skill = self.supervisor_delegation_skill.strip()
        if not supervisor_delegation_skill:
            raise ValueError("Module supervisor delegation skill must not be empty.")

        required_integrations = tuple(self.required_integrations)

        _ensure_unique(skills, label="skills")
        _ensure_unique(agent_sections, label="agent_sections")
        _ensure_unique(tools_allow, label="tools_allow")
        _ensure_unique(tools_deny, label="tools_deny")
        _ensure_unique(supervisor_skills, label="supervisor_skills")
        _ensure_unique(
            [requirement.kind.value for requirement in required_integrations],
            label="required_integrations.kind",
        )

        recommended_integrations = tuple(self.recommended_integrations)
        _ensure_unique(
            [requirement.kind.value for requirement in recommended_integrations],
            label="recommended_integrations.kind",
        )

        required_kinds = {requirement.kind for requirement in required_integrations}
        recommended_kinds = {requirement.kind for requirement in recommended_integrations}
        if required_kinds & recommended_kinds:
            raise ValueError(
                "Integration kind cannot appear in both required and recommended."
            )

        conditional_skills = tuple(self.conditional_skills)
        conditional_skill_names = [skill.skill_name for skill in conditional_skills]
        _ensure_unique(conditional_skill_names, label="conditional_skills")
        _ensure_unique(
            list(skills) + conditional_skill_names,
            label="skills + conditional_skills",
        )

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "skills", skills)
        object.__setattr__(self, "agent_sections", agent_sections)
        object.__setattr__(self, "tools_allow", tools_allow)
        object.__setattr__(self, "tools_deny", tools_deny)
        object.__setattr__(self, "supervisor_skills", supervisor_skills)
        object.__setattr__(self, "supervisor_delegation_skill", supervisor_delegation_skill)
        object.__setattr__(self, "required_integrations", required_integrations)
        object.__setattr__(self, "recommended_integrations", recommended_integrations)
        object.__setattr__(self, "conditional_skills", conditional_skills)
