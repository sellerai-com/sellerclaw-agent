from __future__ import annotations

from sellerclaw_agent.models import (
    AgentModuleDefinition,
    AgentModuleId,
    CapabilityLevelDefinition,
    ConditionalSkill,
    IntegrationKind,
    IntegrationRequirement,
    ModelTier,
    ModuleCapabilityDefinition,
    OperatingMode,
    resolve_capability_mode,
)

SHOPIFY_STORE_MANAGER_MODULE = AgentModuleDefinition(
    id=AgentModuleId.SHOPIFY_STORE_MANAGER,
    name="Shopify Store Manager",
    description=(
        "Manages Shopify stores: products, orders, fulfillment, storefront content, "
        "and theme customization."
    ),
    agent_id="shopify",
    model_tier=ModelTier.SIMPLE,
    required_integrations=(),
    recommended_integrations=(
        IntegrationRequirement(
            kind=IntegrationKind.SHOPIFY_STORE,
            description="Connect a Shopify store",
        ),
    ),
    tools_allow=("group:fs", "exec", "process", "web_fetch", "web_search", "browser"),
    tools_deny=("group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"),
    skills=("store-api", "shopify-api", "shopify-storefront-setup"),
    conditional_skills=(),
    agent_sections=("core",),
    supervisor_delegation_skill="shopify-delegation",
    supervisor_skills=("store-reporting",),
)

EBAY_STORE_MANAGER_MODULE = AgentModuleDefinition(
    id=AgentModuleId.EBAY_STORE_MANAGER,
    name="eBay Store Manager",
    description=(
        "Manages eBay stores: listings, orders, fulfillment, locations, and seller performance."
    ),
    agent_id="ebay",
    model_tier=ModelTier.SIMPLE,
    required_integrations=(),
    recommended_integrations=(
        IntegrationRequirement(
            kind=IntegrationKind.EBAY_STORE,
            description="Connect an eBay store",
        ),
    ),
    tools_allow=("group:fs", "exec", "process", "web_fetch", "web_search", "browser"),
    tools_deny=("group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"),
    skills=("store-api", "ebay-api"),
    conditional_skills=(),
    agent_sections=("core",),
    supervisor_delegation_skill="ebay-delegation",
    supervisor_skills=("store-reporting",),
)

DROPSHIPPING_SUPPLIER_MODULE = AgentModuleDefinition(
    id=AgentModuleId.DROPSHIPPING_SUPPLIER,
    name="Dropshipping Supplier",
    description="Sources products from supplier platforms, manages purchases, and tracks fulfillment.",
    agent_id="supplier",
    model_tier=ModelTier.SIMPLE,
    required_integrations=(),
    recommended_integrations=(
        IntegrationRequirement(
            kind=IntegrationKind.SUPPLIER_CJ,
            description="Connect a CJ Dropshipping account",
        ),
    ),
    tools_allow=("group:fs", "exec", "process", "web_fetch", "web_search", "browser"),
    tools_deny=("group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"),
    skills=("cj-dropshipping",),
    agent_sections=("core",),
    supervisor_delegation_skill="supplier-delegation",
    supervisor_skills=("catalog-management", "order-orchestration"),
)

MARKETING_MANAGER_MODULE = AgentModuleDefinition(
    id=AgentModuleId.MARKETING_MANAGER,
    name="Marketing Manager",
    description=(
        "Manages paid advertising campaigns on Facebook and Google Ads: "
        "campaign creation, optimization, budget management, A/B testing, "
        "audience management, and performance reporting."
    ),
    agent_id="marketing",
    model_tier=ModelTier.COMPLEX,
    required_integrations=(),
    recommended_integrations=(
        IntegrationRequirement(
            kind=IntegrationKind.FACEBOOK_ADS,
            description="Connect Facebook Ads for autonomous campaign management",
        ),
        IntegrationRequirement(
            kind=IntegrationKind.GOOGLE_ADS,
            description="Connect Google Ads for autonomous campaign management",
        ),
    ),
    tools_allow=("group:fs", "exec", "process", "web_fetch", "web_search", "browser"),
    tools_deny=(
        "group:sessions",
        "group:messaging",
        "canvas",
        "nodes",
        "cron",
        "gateway",
    ),
    skills=("facebook-ads-api", "google-ads-api", "product-data-api", "campaign-playbook"),
    conditional_skills=(),
    agent_sections=("core",),
    supervisor_delegation_skill="marketing-delegation",
    supervisor_skills=("ad-performance-reporting",),
)

PRODUCT_SCOUT_MODULE = AgentModuleDefinition(
    id=AgentModuleId.PRODUCT_SCOUT,
    name="Product Scout",
    description=(
        "Researches product opportunities and niches: demand signals (Google Trends, "
        "DataForSEO, SociaVault social/TikTok Shop), niche scoring, competitive intelligence, "
        "and supplier matching before catalog workflows."
    ),
    agent_id="scout",
    model_tier=ModelTier.COMPLEX,
    required_integrations=(),
    recommended_integrations=(
        IntegrationRequirement(
            kind=IntegrationKind.SUPPLIER_ANY,
            description="Connect at least one supplier account for catalog, pricing, stock, and shipping data",
        ),
        IntegrationRequirement(
            kind=IntegrationKind.RESEARCH_TRENDS,
            description="Configure Google Trends in agent settings (trends provider + SerpApi key if needed)",
        ),
        IntegrationRequirement(
            kind=IntegrationKind.RESEARCH_SEO,
            description="Configure DataForSEO in agent settings (or use platform-funded keys when enabled)",
        ),
        IntegrationRequirement(
            kind=IntegrationKind.RESEARCH_SOCIAL,
            description="Configure SociaVault API key in agent settings for social trends and TikTok Shop research",
        ),
    ),
    tools_allow=("group:fs", "exec", "process", "web_fetch", "web_search", "browser"),
    tools_deny=("group:sessions", "group:messaging", "canvas", "nodes", "cron", "gateway"),
    skills=(
        "trend-analysis",
        "niche-data-collection",
        "competitor-research",
        "keyword-research",
        "product-demand-analysis",
        "listing-optimization",
        "supplier-matching",
    ),
    conditional_skills=(
        ConditionalSkill(skill_name="social-trend-discovery", required_integration=IntegrationKind.RESEARCH_SOCIAL),
        ConditionalSkill(skill_name="tiktok-shop-research", required_integration=IntegrationKind.RESEARCH_SOCIAL),
    ),
    agent_sections=("core",),
    supervisor_delegation_skill="product-scout-delegation",
    supervisor_skills=("niche-scoring-delegation", "niche-scoring-report"),
)

MODULE_REGISTRY: dict[AgentModuleId, AgentModuleDefinition] = {
    SHOPIFY_STORE_MANAGER_MODULE.id: SHOPIFY_STORE_MANAGER_MODULE,
    EBAY_STORE_MANAGER_MODULE.id: EBAY_STORE_MANAGER_MODULE,
    DROPSHIPPING_SUPPLIER_MODULE.id: DROPSHIPPING_SUPPLIER_MODULE,
    MARKETING_MANAGER_MODULE.id: MARKETING_MANAGER_MODULE,
    PRODUCT_SCOUT_MODULE.id: PRODUCT_SCOUT_MODULE,
}

MODULE_CAPABILITIES: dict[AgentModuleId, tuple[ModuleCapabilityDefinition, ...]] = {
    AgentModuleId.SHOPIFY_STORE_MANAGER: (
        ModuleCapabilityDefinition(
            id="store_management",
            name="Store Management",
            description="Manages Shopify products, inventory, and reporting.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Full Shopify store operations via API.",
                    enabled_by="Shopify Store connection",
                    required_integrations=(IntegrationKind.SHOPIFY_STORE,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ASSISTED,
                    description="Store operations via agent browser in Shopify Admin.",
                    enabled_by="Browser access",
                    requires_browser=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Text recommendations and planning.",
                    enabled_by=None,
                ),
            ),
        ),
        ModuleCapabilityDefinition(
            id="order_fulfillment",
            name="Order Fulfillment",
            description="The agent handles Shopify order fulfillment and tracking workflows.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Auto-fulfill orders when tracking arrives.",
                    enabled_by="Shopify Store connection",
                    required_integrations=(IntegrationKind.SHOPIFY_STORE,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Order tracking guidance and alerts.",
                    enabled_by=None,
                ),
            ),
        ),
        ModuleCapabilityDefinition(
            id="storefront_content",
            name="Storefront Content",
            description="Creates and manages store pages, collections, and navigation menus.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Create pages, collections, and navigation via API.",
                    enabled_by="Shopify Store connection",
                    required_integrations=(IntegrationKind.SHOPIFY_STORE,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ASSISTED,
                    description="Manage store content via browser in Shopify Admin.",
                    enabled_by="Browser access",
                    requires_browser=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Content structure recommendations and copywriting.",
                    enabled_by=None,
                ),
            ),
        ),
        ModuleCapabilityDefinition(
            id="theme_customization",
            name="Theme Customization",
            description="Customizes store theme: design, layout, colors, and sections.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Full theme customization via Theme API.",
                    enabled_by="Shopify Store + Theme API access",
                    required_integrations=(
                        IntegrationKind.SHOPIFY_STORE,
                        IntegrationKind.SHOPIFY_THEMES,
                    ),
                    require_all_integrations=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ASSISTED,
                    description="Customize theme via browser in Shopify Theme Editor.",
                    enabled_by="Browser access",
                    requires_browser=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Theme setup guidance and design recommendations.",
                    enabled_by=None,
                ),
            ),
        ),
    ),
    AgentModuleId.EBAY_STORE_MANAGER: (
        ModuleCapabilityDefinition(
            id="listing_management",
            name="Listing Management",
            description="Manages eBay listings, inventory, and seller reporting.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Full eBay listing operations via API.",
                    enabled_by="eBay Store connection",
                    required_integrations=(IntegrationKind.EBAY_STORE,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ASSISTED,
                    description="Listing operations via agent browser on eBay.",
                    enabled_by="Browser access",
                    requires_browser=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Text recommendations and planning.",
                    enabled_by=None,
                ),
            ),
        ),
        ModuleCapabilityDefinition(
            id="order_fulfillment",
            name="Order Fulfillment",
            description="The agent handles eBay order fulfillment and tracking workflows.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Auto-fulfill orders when tracking arrives.",
                    enabled_by="eBay Store connection",
                    required_integrations=(IntegrationKind.EBAY_STORE,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Order tracking guidance and alerts.",
                    enabled_by=None,
                ),
            ),
        ),
    ),
    AgentModuleId.DROPSHIPPING_SUPPLIER: (
        ModuleCapabilityDefinition(
            id="product_sourcing",
            name="Product Sourcing",
            description="The agent sources products from supplier platforms.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Search and source products via CJ Dropshipping API.",
                    enabled_by="CJ Dropshipping",
                    required_integrations=(IntegrationKind.SUPPLIER_CJ,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ASSISTED,
                    description="Browse supplier catalogs via agent browser.",
                    enabled_by="Browser access",
                    requires_browser=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Product sourcing recommendations and analysis.",
                    enabled_by=None,
                ),
            ),
        ),
        ModuleCapabilityDefinition(
            id="purchase_management",
            name="Purchase Management",
            description="The agent manages supplier purchases and order tracking.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Auto-purchase and track orders via CJ API.",
                    enabled_by="CJ Dropshipping",
                    required_integrations=(IntegrationKind.SUPPLIER_CJ,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Purchase guidance and order tracking alerts.",
                    enabled_by=None,
                ),
            ),
        ),
    ),
    AgentModuleId.MARKETING_MANAGER: (
        ModuleCapabilityDefinition(
            id="campaign_management",
            name="Campaign Management",
            description="The agent manages ad campaigns across supported platforms.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Full campaign lifecycle via ad platform APIs.",
                    enabled_by="Ad platforms (Facebook, Google)",
                    required_integrations=(
                        IntegrationKind.FACEBOOK_ADS,
                        IntegrationKind.GOOGLE_ADS,
                    ),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Campaign strategy recommendations and analysis.",
                    enabled_by=None,
                ),
            ),
        ),
        ModuleCapabilityDefinition(
            id="performance_optimization",
            name="Performance Optimization",
            description="The agent optimizes ad performance and budget allocation.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Auto-optimize budgets, bids, and creative.",
                    enabled_by="Ad platforms (Facebook, Google)",
                    required_integrations=(
                        IntegrationKind.FACEBOOK_ADS,
                        IntegrationKind.GOOGLE_ADS,
                    ),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Performance analysis and optimization recommendations.",
                    enabled_by=None,
                ),
            ),
        ),
    ),
    AgentModuleId.PRODUCT_SCOUT: (
        ModuleCapabilityDefinition(
            id="demand_and_supply_research",
            name="Demand and supply research",
            description="Validates demand with trends data and matches products to supplier inventory.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description=(
                        "Query demand signals (Google Trends, DataForSEO, and/or SociaVault) and supplier "
                        "catalogs via sellerclaw-api (requires a connected supplier account plus at least one "
                        "research integration)."
                    ),
                    enabled_by="Supplier account and Google Trends, DataForSEO, or SociaVault",
                    required_integrations=(IntegrationKind.SUPPLIER_ANY,),
                    require_all_integrations=True,
                    require_one_of=(
                        IntegrationKind.RESEARCH_TRENDS,
                        IntegrationKind.RESEARCH_SEO,
                        IntegrationKind.RESEARCH_SOCIAL,
                    ),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ASSISTED,
                    description="Research trends sites, marketplaces, and suppliers via browser.",
                    enabled_by="Browser access",
                    requires_browser=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="Recommendations from general market knowledge only.",
                    enabled_by=None,
                ),
            ),
        ),
        ModuleCapabilityDefinition(
            id="competitive_intelligence",
            name="Competitive intelligence",
            description="Analyzes competitor stores, ads, and marketplaces for positioning gaps.",
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Enriched research when supplier API data is available for benchmarks.",
                    enabled_by="Connected supplier account",
                    required_integrations=(IntegrationKind.SUPPLIER_ANY,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ASSISTED,
                    description="Browser-based store, ad library, and marketplace research.",
                    enabled_by="Browser access",
                    requires_browser=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="High-level competitive guidance without live browsing.",
                    enabled_by=None,
                ),
            ),
        ),
        ModuleCapabilityDefinition(
            id="social_commerce_intelligence",
            name="Social commerce intelligence",
            description=(
                "Social-native demand signals, TikTok Shop marketplace data, Reddit community voice, "
                "and ad-library lookups via SociaVault."
            ),
            levels=(
                CapabilityLevelDefinition(
                    mode=OperatingMode.AUTONOMOUS,
                    description="Query /research/social endpoints (SociaVault) through sellerclaw-api.",
                    enabled_by="SociaVault API key (user or corporate)",
                    required_integrations=(IntegrationKind.RESEARCH_SOCIAL,),
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ASSISTED,
                    description="Manual social and marketplace research via browser when API is unavailable.",
                    enabled_by="Browser access",
                    requires_browser=True,
                ),
                CapabilityLevelDefinition(
                    mode=OperatingMode.ADVISORY,
                    description="General guidance on social-commerce research without live data.",
                    enabled_by=None,
                ),
            ),
        ),
    ),
}


def get_module(module_id: AgentModuleId) -> AgentModuleDefinition | None:
    """Return module definition by ID, or None if not found."""
    return MODULE_REGISTRY.get(module_id)


def get_all_modules() -> list[AgentModuleDefinition]:
    """Return all registered module definitions, ordered by ID value."""
    return sorted(MODULE_REGISTRY.values(), key=lambda item: item.id.value)


def get_modules_by_integration(kind: IntegrationKind) -> list[AgentModuleDefinition]:
    """Return modules that declare the given integration kind."""
    return [
        module
        for module in get_all_modules()
        if any(
            requirement.kind == kind
            for requirement in (
                *module.required_integrations,
                *module.recommended_integrations,
            )
        )
    ]


def get_module_capability_definitions(
    module_id: AgentModuleId,
) -> tuple[ModuleCapabilityDefinition, ...]:
    """Return capability definitions for a module, or empty tuple if not found."""
    return MODULE_CAPABILITIES.get(module_id, ())


def resolve_module_operating_mode(
    module_id: AgentModuleId,
    connected_integrations: frozenset[IntegrationKind],
    browser_active: bool,
) -> OperatingMode:
    """Compute the overall operating mode for a module."""
    capabilities = get_module_capability_definitions(module_id)
    if not capabilities:
        return OperatingMode.ADVISORY

    modes = [
        resolve_capability_mode(capability, connected_integrations, browser_active)
        for capability in capabilities
    ]
    if OperatingMode.AUTONOMOUS in modes:
        return OperatingMode.AUTONOMOUS
    if OperatingMode.ASSISTED in modes:
        return OperatingMode.ASSISTED
    return OperatingMode.ADVISORY
