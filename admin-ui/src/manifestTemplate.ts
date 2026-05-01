import type { SaveManifestRequest } from './types/manifest'

export const manifestTemplate: SaveManifestRequest = {
  user_id: '00000000-0000-4000-8000-000000000000',
  litellm_base_url: 'http://litellm:4000',
  litellm_api_key: '',
  agent_api_base_path: '/agent',
  template_variables: {},
  enabled_modules: [],
  connected_integrations: [],
  global_browser_enabled: true,
  per_module_browser: {},
  telegram: {
    enabled: false,
    bot_token: '',
    allowed_user_ids: [],
    allowed_group_ids: [],
  },
  web_search: {
    enabled: false,
  },
  primary_channel: 'sellerclaw-ui',
}
