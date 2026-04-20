<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { getCommandHistory } from '../api/commands'
import { extractErrorMessage } from '../api/agent'
import type { CommandHistoryEntry } from '../types/commands'

const entries = ref<CommandHistoryEntry[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

function formatTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

function outcomeBadgeClass(outcome: string | null): string {
  if (outcome === 'ok') return 'bg-emerald-900/60 text-emerald-300 border border-emerald-700'
  if (outcome && outcome !== 'ok') return 'bg-rose-900/60 text-rose-300 border border-rose-700'
  return 'bg-slate-800 text-slate-400 border border-slate-700'
}

function truncate(value: string | null, max = 60): string {
  if (!value) return '—'
  return value.length > max ? `${value.slice(0, max)}…` : value
}

async function load(): Promise<void> {
  if (loading.value) return
  loading.value = true
  error.value = null
  try {
    entries.value = await getCommandHistory()
  } catch (err) {
    error.value = extractErrorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
  timer = setInterval(() => {
    void load()
  }, 5000)
})

onUnmounted(() => {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
})
</script>

<template>
  <section class="rounded-lg border border-slate-800 bg-slate-900/60 p-5">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h2 class="text-lg font-medium">Command history</h2>
        <p class="text-xs text-slate-400">
          Commands received by the agent from SellerClaw. Refreshes every 5 seconds.
        </p>
      </div>
      <button
        type="button"
        class="rounded bg-slate-800 px-3 py-1 text-sm hover:bg-slate-700 disabled:opacity-50"
        :disabled="loading"
        @click="load"
      >
        {{ loading ? 'Loading…' : 'Refresh' }}
      </button>
    </div>

    <p v-if="error" class="mb-3 text-sm text-rose-400">{{ error }}</p>

    <div v-if="entries.length === 0 && !error" class="rounded border border-dashed border-slate-700 p-6 text-center text-sm text-slate-400">
      No commands yet.
    </div>

    <div v-else-if="entries.length > 0" class="overflow-x-auto">
      <table class="w-full text-left text-sm">
        <thead class="border-b border-slate-800 text-xs uppercase text-slate-400">
          <tr>
            <th class="px-2 py-2">Received</th>
            <th class="px-2 py-2">Command</th>
            <th class="px-2 py-2">Outcome</th>
            <th class="px-2 py-2">Error</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="entry in entries"
            :key="entry.command_id"
            class="border-b border-slate-800/60 align-top"
          >
            <td class="px-2 py-2 text-slate-200">
              <div>{{ formatTime(entry.received_at) }}</div>
              <div class="text-xs text-slate-500">
                created: {{ formatTime(entry.issued_at) }}
              </div>
              <div class="text-xs text-slate-500">
                executed: {{ formatTime(entry.executed_at) }}
              </div>
              <div class="font-mono text-[10px] text-slate-600" :title="entry.command_id">
                {{ entry.command_id.slice(0, 8) }}
              </div>
            </td>
            <td class="px-2 py-2 font-mono text-slate-100">{{ entry.command_type }}</td>
            <td class="px-2 py-2">
              <span
                class="inline-block rounded px-2 py-0.5 text-xs font-medium"
                :class="outcomeBadgeClass(entry.outcome)"
              >
                {{ entry.outcome ?? '—' }}
              </span>
            </td>
            <td class="px-2 py-2 text-xs text-rose-300" :title="entry.error ?? ''">
              {{ truncate(entry.error) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
