export const INTEGRATION_KINDS = [
  'ebay_store',
  'shopify_store',
  'shopify_themes',
  'supplier_cj',
  'supplier_any',
  'facebook_ads',
  'google_ads',
  'research_trends',
  'research_seo',
  'research_social',
] as const

export type IntegrationKind = (typeof INTEGRATION_KINDS)[number]

export const AGENT_MODULE_IDS = [
  'shopify_store_manager',
  'ebay_store_manager',
  'dropshipping_supplier',
  'marketing_manager',
  'product_scout',
] as const

export type AgentModuleId = (typeof AGENT_MODULE_IDS)[number]

export interface ManifestModelSpec {
  id: string
  name: string
  reasoning?: boolean
  input?: string[] | string
  context_window: number
  max_tokens: number
}

export interface ManifestModels {
  complex: ManifestModelSpec
  simple: ManifestModelSpec
}

export interface ManifestTelegram {
  enabled?: boolean
  bot_token?: string
  allowed_user_ids?: string[]
  allowed_group_ids?: string[]
}

export interface ManifestWebSearch {
  enabled?: boolean
  provider?: string | null
  api_key?: string
}

export interface SaveManifestRequest {
  user_id: string
  gateway_token: string
  hooks_token: string
  litellm_base_url: string
  litellm_api_key: string
  models: ManifestModels
  template_variables?: Record<string, string>
  enabled_modules?: AgentModuleId[] | string[]
  connected_integrations?: IntegrationKind[] | string[]
  global_browser_enabled?: boolean
  per_module_browser?: Record<string, boolean>
  telegram?: ManifestTelegram
  web_search?: ManifestWebSearch
  primary_channel?: string
}

export interface SaveManifestResponse {
  status: string
  manifest_path: string
  version: string
}

export interface GetManifestResponse {
  manifest: Record<string, unknown>
  version: string
}
