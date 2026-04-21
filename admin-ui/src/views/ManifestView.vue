<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { extractErrorMessage, getManifest, saveManifest } from '../api/agent'
import { getAuthStatus } from '../api/auth'
import { manifestTemplate } from '../manifestTemplate'
import ModelSpecFields from '../components/ModelSpecFields.vue'
import {
  AGENT_MODULE_IDS,
  INTEGRATION_KINDS,
  type AgentModuleId,
  type IntegrationKind,
  type ManifestModelSpec,
  type ManifestTelegram,
  type ManifestWebSearch,
  type SaveManifestRequest,
} from '../types/manifest'

type Status = { kind: 'idle' } | { kind: 'ok'; message: string } | { kind: 'err'; message: string }

const MODULE_LABELS: Record<AgentModuleId, string> = {
  shopify_store_manager: 'Shopify Store Manager',
  ebay_store_manager: 'eBay Store Manager',
  dropshipping_supplier: 'Dropshipping Supplier',
  marketing_manager: 'Marketing Manager',
  product_scout: 'Product Scout',
}

const INTEGRATION_LABELS: Record<IntegrationKind, string> = {
  ebay_store: 'eBay Store',
  shopify_store: 'Shopify Store',
  shopify_themes: 'Shopify Themes',
  supplier_cj: 'Supplier: CJ',
  supplier_any: 'Supplier: Any',
  facebook_ads: 'Facebook Ads',
  google_ads: 'Google Ads',
  research_trends: 'Research: Trends',
  research_seo: 'Research: SEO',
  research_social: 'Research: Social',
}

const WEB_SEARCH_PROVIDERS = ['tavily', 'brave', 'exa'] as const

interface KVRow {
  key: string
  value: string
}

interface BrowserRow {
  module: string
  enabled: boolean
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

function cloneTemplate(): SaveManifestRequest {
  return deepClone(manifestTemplate)
}

const form = reactive<SaveManifestRequest>(cloneTemplate())
const templateVarsRows = ref<KVRow[]>([])
const browserRows = ref<BrowserRow[]>([])
const telegramUserIdsText = ref('')
const telegramGroupIdsText = ref('')
const showGatewayToken = ref(false)
const showHooksToken = ref(false)
const showLitellmKey = ref(false)
const showWebSearchKey = ref(false)
const showTelegramToken = ref(false)

const currentVersion = ref<string | null>(null)
const loading = ref(false)
const saving = ref(false)
const showRawJson = ref(false)
const status = ref<Status>({ kind: 'idle' })
const userIdAutoFilled = ref(false)

function readStatus(): Status {
  return status.value
}

const NULL_UUID = '00000000-0000-4000-8000-000000000000'

function ensureTelegram(value: ManifestTelegram | undefined): ManifestTelegram {
  return {
    enabled: value?.enabled ?? false,
    bot_token: value?.bot_token ?? '',
    allowed_user_ids: value?.allowed_user_ids ?? [],
    allowed_group_ids: value?.allowed_group_ids ?? [],
  }
}

function ensureWebSearch(value: ManifestWebSearch | undefined): ManifestWebSearch {
  return {
    enabled: value?.enabled ?? false,
    provider: value?.provider ?? null,
    api_key: value?.api_key ?? '',
  }
}

function ensureModelSpec(
  value: ManifestModelSpec | undefined,
  fallback: ManifestModelSpec,
): ManifestModelSpec {
  return {
    id: value?.id ?? fallback.id,
    name: value?.name ?? fallback.name,
    reasoning: value?.reasoning ?? fallback.reasoning ?? false,
    input: value?.input ?? fallback.input ?? ['text'],
    context_window: value?.context_window ?? fallback.context_window,
    max_tokens: value?.max_tokens ?? fallback.max_tokens,
  }
}

function rowsFromRecord(record: Record<string, string> | undefined): KVRow[] {
  return Object.entries(record ?? {}).map(([key, value]) => ({ key, value: String(value) }))
}

function recordFromRows(rows: KVRow[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const { key, value } of rows) {
    const k = key.trim()
    if (k) out[k] = value
  }
  return out
}

function browserRowsFromRecord(record: Record<string, boolean> | undefined): BrowserRow[] {
  return Object.entries(record ?? {}).map(([module, enabled]) => ({
    module,
    enabled: Boolean(enabled),
  }))
}

function recordFromBrowserRows(rows: BrowserRow[]): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  for (const { module, enabled } of rows) {
    const m = module.trim()
    if (m) out[m] = enabled
  }
  return out
}

function splitIds(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function joinIds(list: string[] | undefined): string {
  return (list ?? []).join('\n')
}

function loadIntoForm(data: Record<string, unknown>): void {
  const tpl = cloneTemplate()
  const raw = data as Partial<SaveManifestRequest>

  form.user_id = typeof raw.user_id === 'string' ? raw.user_id : tpl.user_id
  form.gateway_token = raw.gateway_token ?? ''
  form.hooks_token = raw.hooks_token ?? ''
  form.litellm_base_url = raw.litellm_base_url ?? tpl.litellm_base_url
  form.litellm_api_key = raw.litellm_api_key ?? ''
  form.primary_channel = raw.primary_channel ?? tpl.primary_channel
  form.global_browser_enabled = raw.global_browser_enabled ?? true

  form.models = {
    complex: ensureModelSpec(raw.models?.complex, tpl.models.complex),
    simple: ensureModelSpec(raw.models?.simple, tpl.models.simple),
  }

  form.enabled_modules = Array.isArray(raw.enabled_modules)
    ? [...(raw.enabled_modules as string[])]
    : []
  form.connected_integrations = Array.isArray(raw.connected_integrations)
    ? [...(raw.connected_integrations as string[])]
    : []

  form.template_variables = { ...(raw.template_variables ?? {}) }
  form.per_module_browser = { ...(raw.per_module_browser ?? {}) }
  form.telegram = ensureTelegram(raw.telegram)
  form.web_search = ensureWebSearch(raw.web_search)

  templateVarsRows.value = rowsFromRecord(form.template_variables)
  browserRows.value = browserRowsFromRecord(form.per_module_browser)
  telegramUserIdsText.value = joinIds(form.telegram.allowed_user_ids)
  telegramGroupIdsText.value = joinIds(form.telegram.allowed_group_ids)
}

function resetFromTemplate(): void {
  const tpl = cloneTemplate()
  Object.assign(form, tpl)
  templateVarsRows.value = rowsFromRecord(tpl.template_variables)
  browserRows.value = browserRowsFromRecord(tpl.per_module_browser)
  telegramUserIdsText.value = joinIds(tpl.telegram?.allowed_user_ids)
  telegramGroupIdsText.value = joinIds(tpl.telegram?.allowed_group_ids)
}

async function maybeAutoFillUserId(): Promise<void> {
  if (form.user_id && form.user_id !== NULL_UUID) return
  try {
    const auth = await getAuthStatus()
    if (auth.connected && auth.user_id) {
      form.user_id = auth.user_id
      userIdAutoFilled.value = true
    }
  } catch {
    // swallow — user_id stays as-is
  }
}

async function refresh(): Promise<void> {
  loading.value = true
  status.value = { kind: 'idle' }
  userIdAutoFilled.value = false
  try {
    const data = await getManifest()
    if (data) {
      loadIntoForm(data.manifest)
      currentVersion.value = data.version
    } else {
      resetFromTemplate()
      currentVersion.value = null
    }
    await maybeAutoFillUserId()
  } catch (err) {
    status.value = { kind: 'err', message: extractErrorMessage(err) }
  } finally {
    loading.value = false
  }
}

function toggleInArray<T extends string>(list: T[], value: T, checked: boolean): T[] {
  const set = new Set(list)
  if (checked) set.add(value)
  else set.delete(value)
  return [...set]
}

function toggleModule(id: AgentModuleId, checked: boolean): void {
  form.enabled_modules = toggleInArray(
    (form.enabled_modules ?? []) as AgentModuleId[],
    id,
    checked,
  )
}

function toggleIntegration(kind: IntegrationKind, checked: boolean): void {
  form.connected_integrations = toggleInArray(
    (form.connected_integrations ?? []) as IntegrationKind[],
    kind,
    checked,
  )
}

function addTemplateVar(): void {
  templateVarsRows.value.push({ key: '', value: '' })
}
function removeTemplateVar(index: number): void {
  templateVarsRows.value.splice(index, 1)
}
function addBrowserRow(): void {
  browserRows.value.push({ module: '', enabled: true })
}
function removeBrowserRow(index: number): void {
  browserRows.value.splice(index, 1)
}

function buildPayload(): SaveManifestRequest {
  const complex = form.models.complex
  const simple = form.models.simple
  return {
    user_id: form.user_id.trim(),
    gateway_token: form.gateway_token,
    hooks_token: form.hooks_token,
    litellm_base_url: form.litellm_base_url,
    litellm_api_key: form.litellm_api_key,
    primary_channel: form.primary_channel,
    global_browser_enabled: form.global_browser_enabled,
    models: {
      complex: { ...complex, input: normalizeInput(complex.input) },
      simple: { ...simple, input: normalizeInput(simple.input) },
    },
    enabled_modules: [...(form.enabled_modules ?? [])],
    connected_integrations: [...(form.connected_integrations ?? [])],
    template_variables: recordFromRows(templateVarsRows.value),
    per_module_browser: recordFromBrowserRows(browserRows.value),
    telegram: {
      enabled: form.telegram?.enabled ?? false,
      bot_token: form.telegram?.bot_token ?? '',
      allowed_user_ids: splitIds(telegramUserIdsText.value),
      allowed_group_ids: splitIds(telegramGroupIdsText.value),
    },
    web_search: {
      enabled: form.web_search?.enabled ?? false,
      provider: form.web_search?.provider ?? null,
      api_key: form.web_search?.api_key ?? '',
    },
  }
}

function normalizeInput(input: string[] | string | undefined): string[] {
  if (input === undefined) return ['text']
  if (typeof input === 'string') return [input]
  return input.length > 0 ? [...input] : ['text']
}

const rawJsonPreview = computed(() => JSON.stringify(buildPayload(), null, 2))

function validate(): string | null {
  if (!form.user_id || !form.user_id.trim()) return 'User ID is required.'
  if (form.models.complex.context_window <= 0) return 'Complex.context_window must be > 0.'
  if (form.models.complex.max_tokens <= 0) return 'Complex.max_tokens must be > 0.'
  if (form.models.simple.context_window <= 0) return 'Simple.context_window must be > 0.'
  if (form.models.simple.max_tokens <= 0) return 'Simple.max_tokens must be > 0.'
  return null
}

async function submit(): Promise<void> {
  status.value = { kind: 'idle' }
  const err = validate()
  if (err) {
    status.value = { kind: 'err', message: err }
    return
  }
  saving.value = true
  try {
    const resp = await saveManifest(buildPayload())
    currentVersion.value = resp.version
    await refresh()
    const afterReload = readStatus()
    if (afterReload.kind === 'err') {
      status.value = {
        kind: 'err',
        message: `Saved (version ${resp.version}), but failed to reload manifest: ${afterReload.message}`,
      }
      return
    }
    status.value = { kind: 'ok', message: `Saved. version=${resp.version}` }
  } catch (err) {
    status.value = { kind: 'err', message: extractErrorMessage(err) }
  } finally {
    saving.value = false
  }
}

function updateComplexModel(value: ManifestModelSpec): void {
  form.models.complex = value
}
function updateSimpleModel(value: ManifestModelSpec): void {
  form.models.simple = value
}

onMounted(refresh)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-lg font-medium">Manifest</h2>
        <p class="text-xs text-slate-400">Agent bundle configuration.</p>
      </div>
      <div class="flex items-center gap-3">
        <span
          v-if="currentVersion"
          class="rounded bg-slate-800 px-2 py-1 font-mono text-xs text-emerald-300"
        >
          version: {{ currentVersion }}
        </span>
        <button
          type="button"
          class="rounded bg-slate-800 px-3 py-1 text-sm hover:bg-slate-700 disabled:opacity-50"
          :disabled="loading"
          @click="refresh"
        >
          {{ loading ? 'Loading…' : 'Reload' }}
        </button>
      </div>
    </div>

    <form class="space-y-6" @submit.prevent="submit">
      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">Identity &amp; tokens</legend>
        <div class="grid gap-3 md:grid-cols-2">
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">User ID</span>
            <input
              v-model="form.user_id"
              type="text"
              class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 font-mono text-sm text-slate-100 outline-none focus:border-emerald-600"
            />
            <span v-if="userIdAutoFilled" class="mt-1 block text-[11px] text-emerald-400">
              Filled from the signed-in user.
            </span>
          </label>
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">Primary channel</span>
            <input
              v-model="form.primary_channel"
              type="text"
              class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
            />
          </label>
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">Gateway token</span>
            <div class="flex gap-2">
              <input
                v-model="form.gateway_token"
                :type="showGatewayToken ? 'text' : 'password'"
                class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
              />
              <button
                type="button"
                class="rounded bg-slate-800 px-2 text-xs hover:bg-slate-700"
                @click="showGatewayToken = !showGatewayToken"
              >
                {{ showGatewayToken ? 'Hide' : 'Show' }}
              </button>
            </div>
          </label>
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">Hooks token</span>
            <div class="flex gap-2">
              <input
                v-model="form.hooks_token"
                :type="showHooksToken ? 'text' : 'password'"
                class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
              />
              <button
                type="button"
                class="rounded bg-slate-800 px-2 text-xs hover:bg-slate-700"
                @click="showHooksToken = !showHooksToken"
              >
                {{ showHooksToken ? 'Hide' : 'Show' }}
              </button>
            </div>
          </label>
        </div>
      </fieldset>

      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">LiteLLM</legend>
        <div class="grid gap-3 md:grid-cols-2">
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">LiteLLM base URL</span>
            <input
              v-model="form.litellm_base_url"
              type="text"
              class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
            />
          </label>
          <label class="block text-xs md:col-span-2">
            <span class="mb-1 block text-slate-400">LiteLLM API key</span>
            <div class="flex gap-2">
              <input
                v-model="form.litellm_api_key"
                :type="showLitellmKey ? 'text' : 'password'"
                class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
              />
              <button
                type="button"
                class="rounded bg-slate-800 px-2 text-xs hover:bg-slate-700"
                @click="showLitellmKey = !showLitellmKey"
              >
                {{ showLitellmKey ? 'Hide' : 'Show' }}
              </button>
            </div>
          </label>
        </div>
      </fieldset>

      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">Models</legend>
        <div class="grid gap-3 md:grid-cols-2">
          <ModelSpecFields
            :model-value="form.models.complex"
            title="Complex"
            @update:model-value="updateComplexModel"
          />
          <ModelSpecFields
            :model-value="form.models.simple"
            title="Simple"
            @update:model-value="updateSimpleModel"
          />
        </div>
      </fieldset>

      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">Modules</legend>
        <div class="grid gap-2 md:grid-cols-2">
          <label
            v-for="id in AGENT_MODULE_IDS"
            :key="id"
            class="flex items-center gap-2 text-sm text-slate-200"
          >
            <input
              type="checkbox"
              :checked="(form.enabled_modules ?? []).includes(id)"
              @change="toggleModule(id, ($event.target as HTMLInputElement).checked)"
            />
            {{ MODULE_LABELS[id] }}
            <span class="text-xs text-slate-500">({{ id }})</span>
          </label>
        </div>
      </fieldset>

      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">Integrations</legend>
        <div class="grid gap-2 md:grid-cols-2">
          <label
            v-for="kind in INTEGRATION_KINDS"
            :key="kind"
            class="flex items-center gap-2 text-sm text-slate-200"
          >
            <input
              type="checkbox"
              :checked="(form.connected_integrations ?? []).includes(kind)"
              @change="toggleIntegration(kind, ($event.target as HTMLInputElement).checked)"
            />
            {{ INTEGRATION_LABELS[kind] }}
            <span class="text-xs text-slate-500">({{ kind }})</span>
          </label>
        </div>
      </fieldset>

      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">Browser</legend>
        <label class="flex items-center gap-2 text-sm text-slate-200">
          <input
            v-model="form.global_browser_enabled"
            type="checkbox"
          />
          Global browser enabled
        </label>
        <div class="space-y-2">
          <div class="text-xs text-slate-400">Per-module browser:</div>
          <div
            v-for="(row, idx) in browserRows"
            :key="idx"
            class="flex items-center gap-2"
          >
            <input
              v-model="row.module"
              type="text"
              placeholder="module id"
              class="flex-1 rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
            />
            <label class="flex items-center gap-1 text-xs text-slate-200">
              <input v-model="row.enabled" type="checkbox" />
              enabled
            </label>
            <button
              type="button"
              class="rounded bg-slate-800 px-2 py-1 text-xs hover:bg-slate-700"
              @click="removeBrowserRow(idx)"
            >
              Remove
            </button>
          </div>
          <button
            type="button"
            class="rounded bg-slate-800 px-3 py-1 text-xs hover:bg-slate-700"
            @click="addBrowserRow"
          >
            + Add row
          </button>
        </div>
      </fieldset>

      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">Template variables</legend>
        <div
          v-for="(row, idx) in templateVarsRows"
          :key="idx"
          class="flex items-center gap-2"
        >
          <input
            v-model="row.key"
            type="text"
            placeholder="key"
            class="w-1/3 rounded border border-slate-800 bg-slate-950 px-2 py-1 font-mono text-sm text-slate-100 outline-none focus:border-emerald-600"
          />
          <input
            v-model="row.value"
            type="text"
            placeholder="value"
            class="flex-1 rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
          />
          <button
            type="button"
            class="rounded bg-slate-800 px-2 py-1 text-xs hover:bg-slate-700"
            @click="removeTemplateVar(idx)"
          >
            Remove
          </button>
        </div>
        <button
          type="button"
          class="rounded bg-slate-800 px-3 py-1 text-xs hover:bg-slate-700"
          @click="addTemplateVar"
        >
          + Add row
        </button>
      </fieldset>

      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">Telegram</legend>
        <label class="flex items-center gap-2 text-sm text-slate-200">
          <input
            :checked="form.telegram?.enabled ?? false"
            type="checkbox"
            @change="
              form.telegram = {
                ...(form.telegram ?? { bot_token: '', allowed_user_ids: [], allowed_group_ids: [] }),
                enabled: ($event.target as HTMLInputElement).checked,
              }
            "
          />
          Enabled
        </label>
        <label class="block text-xs">
          <span class="mb-1 block text-slate-400">Bot token</span>
          <div class="flex gap-2">
            <input
              :value="form.telegram?.bot_token ?? ''"
              :type="showTelegramToken ? 'text' : 'password'"
              class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
              @input="
                form.telegram = {
                  ...(form.telegram ?? { enabled: false, allowed_user_ids: [], allowed_group_ids: [] }),
                  bot_token: ($event.target as HTMLInputElement).value,
                }
              "
            />
            <button
              type="button"
              class="rounded bg-slate-800 px-2 text-xs hover:bg-slate-700"
              @click="showTelegramToken = !showTelegramToken"
            >
              {{ showTelegramToken ? 'Hide' : 'Show' }}
            </button>
          </div>
        </label>
        <div class="grid gap-3 md:grid-cols-2">
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">Allowed user IDs (one per line)</span>
            <textarea
              v-model="telegramUserIdsText"
              rows="3"
              class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 font-mono text-sm text-slate-100 outline-none focus:border-emerald-600"
            />
          </label>
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">Allowed group IDs (one per line)</span>
            <textarea
              v-model="telegramGroupIdsText"
              rows="3"
              class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 font-mono text-sm text-slate-100 outline-none focus:border-emerald-600"
            />
          </label>
        </div>
      </fieldset>

      <fieldset class="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
        <legend class="px-2 text-sm font-medium text-slate-200">Web search</legend>
        <label class="flex items-center gap-2 text-sm text-slate-200">
          <input
            :checked="form.web_search?.enabled ?? false"
            type="checkbox"
            @change="
              form.web_search = {
                ...(form.web_search ?? { provider: null, api_key: '' }),
                enabled: ($event.target as HTMLInputElement).checked,
              }
            "
          />
          Enabled
        </label>
        <div class="grid gap-3 md:grid-cols-2">
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">Provider</span>
            <select
              :value="form.web_search?.provider ?? ''"
              class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
              @change="
                form.web_search = {
                  ...(form.web_search ?? { enabled: false, api_key: '' }),
                  provider: ($event.target as HTMLSelectElement).value || null,
                }
              "
            >
              <option value="">—</option>
              <option v-for="p in WEB_SEARCH_PROVIDERS" :key="p" :value="p">{{ p }}</option>
            </select>
          </label>
          <label class="block text-xs">
            <span class="mb-1 block text-slate-400">API key</span>
            <div class="flex gap-2">
              <input
                :value="form.web_search?.api_key ?? ''"
                :type="showWebSearchKey ? 'text' : 'password'"
                class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
                @input="
                  form.web_search = {
                    ...(form.web_search ?? { enabled: false, provider: null }),
                    api_key: ($event.target as HTMLInputElement).value,
                  }
                "
              />
              <button
                type="button"
                class="rounded bg-slate-800 px-2 text-xs hover:bg-slate-700"
                @click="showWebSearchKey = !showWebSearchKey"
              >
                {{ showWebSearchKey ? 'Hide' : 'Show' }}
              </button>
            </div>
          </label>
        </div>
      </fieldset>

      <div class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/60 p-4">
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="rounded bg-slate-800 px-3 py-1 text-sm hover:bg-slate-700"
            @click="showRawJson = !showRawJson"
          >
            {{ showRawJson ? 'Hide JSON' : 'Show JSON' }}
          </button>
        </div>
        <div class="flex items-center gap-3">
          <span v-if="status.kind === 'ok'" class="text-sm text-emerald-400">{{ status.message }}</span>
          <span v-else-if="status.kind === 'err'" class="text-sm text-rose-400">{{ status.message }}</span>
          <button
            type="submit"
            class="rounded bg-emerald-600 px-4 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            :disabled="saving"
          >
            {{ saving ? 'Saving…' : 'Save' }}
          </button>
        </div>
      </div>

      <section
        v-if="showRawJson"
        class="rounded-lg border border-slate-800 bg-slate-900/60 p-4"
      >
        <div class="mb-2 text-xs uppercase text-slate-400">Raw JSON (preview)</div>
        <pre class="max-h-96 overflow-auto rounded bg-slate-950 p-3 font-mono text-xs text-slate-200">{{ rawJsonPreview }}</pre>
      </section>
    </form>
  </div>
</template>
