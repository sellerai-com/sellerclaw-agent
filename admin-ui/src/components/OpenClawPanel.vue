<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { extractErrorMessage } from '../api/agent'
import { getOpenClawStatus, restartOpenClaw, startOpenClaw, stopOpenClaw } from '../api/openclaw'
import type { OpenClawStatusResponse } from '../types/openclaw'
import { formatUptime, statusLabel } from '../utils/openclaw'

const status = ref<OpenClawStatusResponse | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
const message = ref<string | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null

const isRunning = computed(() => status.value?.status === 'running')
const isStarting = computed(() => status.value?.status === 'starting')

async function refresh(options: { force?: boolean; keepError?: boolean } = {}): Promise<void> {
  if (busy.value && !options.force) {
    return
  }
  message.value = null
  loading.value = true
  if (!options.keepError) {
    error.value = null
  }
  try {
    status.value = await getOpenClawStatus()
    if (!options.keepError) {
      error.value = null
    }
  } catch (err) {
    error.value = extractErrorMessage(err)
    status.value = null
  } finally {
    loading.value = false
  }
}

async function onStart(): Promise<void> {
  busy.value = true
  message.value = null
  error.value = null
  try {
    const r = await startOpenClaw()
    if (r.outcome === 'completed') {
      await refresh({ force: true })
      message.value = 'OpenClaw start completed.'
    } else {
      error.value = r.error ?? r.outcome
      await refresh({ force: true, keepError: true })
    }
  } catch (err) {
    error.value = extractErrorMessage(err)
  } finally {
    busy.value = false
  }
}

async function onStop(): Promise<void> {
  busy.value = true
  message.value = null
  error.value = null
  try {
    const r = await stopOpenClaw()
    if (r.outcome === 'completed') {
      await refresh({ force: true })
      message.value = 'OpenClaw stopped.'
    } else {
      error.value = r.error ?? r.outcome
      await refresh({ force: true, keepError: true })
    }
  } catch (err) {
    error.value = extractErrorMessage(err)
  } finally {
    busy.value = false
  }
}

async function onRestart(): Promise<void> {
  busy.value = true
  message.value = null
  error.value = null
  try {
    const r = await restartOpenClaw()
    if (r.outcome === 'completed') {
      await refresh({ force: true })
      message.value = 'OpenClaw restarted.'
    } else {
      error.value = r.error ?? r.outcome
      await refresh({ force: true, keepError: true })
    }
  } catch (err) {
    error.value = extractErrorMessage(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void refresh()
  pollTimer = setInterval(() => {
    void refresh()
  }, 8000)
})

onUnmounted(() => {
  if (pollTimer != null) clearInterval(pollTimer)
})
</script>

<template>
  <section class="rounded-lg border border-slate-800 bg-slate-900/60 p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h2 class="text-sm font-medium text-slate-200">OpenClaw container</h2>
      <button
        type="button"
        class="rounded bg-slate-800 px-2 py-1 text-xs hover:bg-slate-700 disabled:opacity-50"
        :disabled="loading || busy"
        @click="refresh({ force: true })"
      >
        Refresh
      </button>
    </div>

    <p v-if="loading && !status" class="mt-2 text-sm text-slate-400">Loading status…</p>

    <div v-else-if="status" class="mt-3 space-y-2 text-sm">
      <div class="flex flex-wrap items-center gap-2">
        <span
          class="inline-block h-2.5 w-2.5 rounded-full"
          :class="{
            'bg-emerald-500': status.status === 'running',
            'bg-amber-500': status.status === 'stopped' || status.status === 'starting',
            'bg-rose-500': status.status === 'error',
          }"
        />
        <span class="font-medium text-slate-100">{{ statusLabel(status.status) }}</span>
        <span v-if="status.container_id" class="font-mono text-xs text-slate-500">
          {{ status.container_id }}
        </span>
      </div>
      <div v-if="status.image" class="text-slate-400">
        Image: <span class="font-mono text-slate-300">{{ status.image }}</span>
      </div>
      <div v-if="status.uptime_seconds != null && status.status === 'running'" class="text-slate-400">
        Uptime: {{ formatUptime(status.uptime_seconds) }}
      </div>
      <div v-if="status.ports" class="text-xs text-slate-500">
        Ports: gateway {{ status.ports.gateway }}, VNC {{ status.ports.vnc }}
      </div>
      <div v-if="status.error" class="text-sm text-rose-400">{{ status.error }}</div>
    </div>

    <div class="mt-4 flex flex-wrap gap-2">
      <button
        type="button"
        class="rounded bg-emerald-700 px-3 py-1.5 text-sm hover:bg-emerald-600 disabled:opacity-50"
        :disabled="busy || isRunning || isStarting"
        @click="onStart"
      >
        Start
      </button>
      <button
        type="button"
        class="rounded bg-slate-700 px-3 py-1.5 text-sm hover:bg-slate-600 disabled:opacity-50"
        :disabled="busy || !isRunning"
        @click="onStop"
      >
        Stop
      </button>
      <button
        type="button"
        class="rounded bg-slate-700 px-3 py-1.5 text-sm hover:bg-slate-600 disabled:opacity-50"
        :disabled="busy || !isRunning"
        @click="onRestart"
      >
        Restart
      </button>
    </div>

    <p v-if="message" class="mt-2 text-sm text-emerald-400">{{ message }}</p>
    <p v-if="error" class="mt-2 text-sm text-rose-400">{{ error }}</p>
  </section>
</template>
