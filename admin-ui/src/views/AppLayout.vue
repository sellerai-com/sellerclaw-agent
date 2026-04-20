<script setup lang="ts">
import { ref } from 'vue'
import ConnectionPanel from '../components/ConnectionPanel.vue'
import OpenClawPanel from '../components/OpenClawPanel.vue'
import ManifestView from './ManifestView.vue'
import CommandHistoryView from './CommandHistoryView.vue'

type Tab = 'manifest' | 'commands'

const activeTab = ref<Tab>('manifest')
</script>

<template>
  <div class="mx-auto max-w-5xl space-y-6 p-8">
    <header>
      <h1 class="text-2xl font-semibold">SellerClaw Agent Admin</h1>
      <p class="text-sm text-slate-400">
        Manage the agent bundle manifest and inspect commands from the SellerClaw server.
      </p>
    </header>

    <ConnectionPanel />

    <OpenClawPanel />

    <nav class="flex gap-2 border-b border-slate-800">
      <button
        type="button"
        class="rounded-t px-4 py-2 text-sm"
        :class="activeTab === 'manifest' ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-slate-200'"
        @click="activeTab = 'manifest'"
      >
        Manifest
      </button>
      <button
        type="button"
        class="rounded-t px-4 py-2 text-sm"
        :class="activeTab === 'commands' ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-slate-200'"
        @click="activeTab = 'commands'"
      >
        Commands
      </button>
    </nav>

    <ManifestView v-if="activeTab === 'manifest'" />
    <CommandHistoryView v-else />
  </div>
</template>
