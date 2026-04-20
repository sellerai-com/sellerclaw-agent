<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { connectSellerClaw, disconnectSellerClaw, getAuthStatus } from '../api/auth'
import { extractErrorMessage } from '../api/agent'
import type { AuthStatusResponse } from '../types/auth'

const status = ref<AuthStatusResponse | null>(null)
const loading = ref(false)
const connecting = ref(false)
const disconnecting = ref(false)
const error = ref<string | null>(null)
const email = ref('')
const password = ref('')

const connected = computed(() => status.value?.connected === true)

function formatConnectedAt(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

async function refreshStatus(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    status.value = await getAuthStatus()
  } catch (err) {
    error.value = extractErrorMessage(err)
    status.value = null
  } finally {
    loading.value = false
  }
}

async function onConnect(): Promise<void> {
  error.value = null
  connecting.value = true
  try {
    await connectSellerClaw({
      email: email.value.trim(),
      password: password.value,
    })
    password.value = ''
    await refreshStatus()
  } catch (err) {
    error.value = extractErrorMessage(err)
  } finally {
    connecting.value = false
  }
}

async function onDisconnect(): Promise<void> {
  error.value = null
  disconnecting.value = true
  try {
    await disconnectSellerClaw()
    await refreshStatus()
  } catch (err) {
    error.value = extractErrorMessage(err)
  } finally {
    disconnecting.value = false
  }
}

onMounted(refreshStatus)
</script>

<template>
  <section class="rounded-lg border border-slate-800 bg-slate-900/60 p-5">
    <p v-if="loading && !status" class="text-sm text-slate-400">Checking status…</p>

    <template v-else-if="connected && status">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="space-y-1 text-sm">
          <div class="flex items-center gap-2">
            <span class="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" />
            <span class="font-medium text-slate-100">Connected to SellerClaw</span>
          </div>
          <div class="text-slate-300">
            User:
            <span class="font-mono text-slate-100">{{ status.user_email ?? '—' }}</span>
            <span v-if="status.user_name" class="text-slate-400"> ({{ status.user_name }})</span>
          </div>
          <div class="text-slate-400">
            Connected at: {{ formatConnectedAt(status.connected_at) }}
          </div>
        </div>
        <button
          type="button"
          class="rounded bg-slate-800 px-3 py-1 text-sm hover:bg-slate-700 disabled:opacity-50"
          :disabled="disconnecting"
          @click="onDisconnect"
        >
          {{ disconnecting ? 'Signing out…' : 'Sign out' }}
        </button>
      </div>
      <p v-if="error" class="mt-3 text-sm text-rose-400">{{ error }}</p>
    </template>

    <template v-else>
      <div class="flex items-center gap-2 text-sm">
        <span class="inline-block h-2.5 w-2.5 rounded-full bg-rose-500" />
        <span class="font-medium text-slate-100">Not connected to SellerClaw</span>
      </div>
      <p class="mt-1 text-xs text-slate-400">
        Sign in with a SellerClaw user so the agent runs on their behalf.
        The API URL is set by the agent build
        (<code class="rounded bg-slate-800 px-1">SELLERCLAW_API_URL</code>).
      </p>
      <form class="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto]" @submit.prevent="onConnect">
        <div>
          <label class="mb-1 block text-xs text-slate-400" for="cp-email">E-mail</label>
          <input
            id="cp-email"
            v-model="email"
            type="email"
            autocomplete="username"
            required
            class="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-600"
          />
        </div>
        <div>
          <label class="mb-1 block text-xs text-slate-400" for="cp-password">Password</label>
          <input
            id="cp-password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            class="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-600"
          />
        </div>
        <div class="flex items-end">
          <button
            type="submit"
            class="h-[38px] rounded bg-emerald-600 px-4 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            :disabled="connecting"
          >
            {{ connecting ? 'Connecting…' : 'Connect' }}
          </button>
        </div>
      </form>
      <p v-if="error" class="mt-3 text-sm text-rose-400">{{ error }}</p>
    </template>
  </section>
</template>
