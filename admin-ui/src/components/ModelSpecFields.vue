<script setup lang="ts">
import { computed } from 'vue'
import type { ManifestModelSpec } from '../types/manifest'

const props = defineProps<{
  modelValue: ManifestModelSpec
  title: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: ManifestModelSpec): void
}>()

const INPUT_MODES = ['text', 'image', 'audio'] as const
type InputMode = (typeof INPUT_MODES)[number]

function normalizedInput(raw: string[] | string | undefined): string[] {
  if (raw === undefined) return ['text']
  if (typeof raw === 'string') return [raw]
  return [...raw]
}

const inputList = computed<string[]>(() => normalizedInput(props.modelValue.input))

function update<K extends keyof ManifestModelSpec>(key: K, value: ManifestModelSpec[K]): void {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function toggleInput(mode: InputMode, checked: boolean): void {
  const set = new Set(inputList.value)
  if (checked) set.add(mode)
  else set.delete(mode)
  const next = INPUT_MODES.filter((m) => set.has(m))
  update('input', next.length > 0 ? next : ['text'])
}

function isChecked(mode: InputMode): boolean {
  return inputList.value.includes(mode)
}
</script>

<template>
  <fieldset class="rounded border border-slate-800 bg-slate-950/40 p-4">
    <legend class="px-2 text-sm font-medium text-slate-200">{{ title }}</legend>
    <div class="grid gap-3 md:grid-cols-2">
      <label class="block text-xs">
        <span class="mb-1 block text-slate-400">ID</span>
        <input
          :value="modelValue.id"
          type="text"
          class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 font-mono text-sm text-slate-100 outline-none focus:border-emerald-600"
          @input="update('id', ($event.target as HTMLInputElement).value)"
        />
      </label>
      <label class="block text-xs">
        <span class="mb-1 block text-slate-400">Name</span>
        <input
          :value="modelValue.name"
          type="text"
          class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
          @input="update('name', ($event.target as HTMLInputElement).value)"
        />
      </label>
      <label class="block text-xs">
        <span class="mb-1 block text-slate-400">Context window</span>
        <input
          :value="modelValue.context_window"
          type="number"
          min="0"
          class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
          @input="update('context_window', Number(($event.target as HTMLInputElement).value))"
        />
      </label>
      <label class="block text-xs">
        <span class="mb-1 block text-slate-400">Max tokens</span>
        <input
          :value="modelValue.max_tokens"
          type="number"
          min="1"
          class="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-sm text-slate-100 outline-none focus:border-emerald-600"
          @input="update('max_tokens', Number(($event.target as HTMLInputElement).value))"
        />
      </label>
    </div>
    <div class="mt-3 flex flex-wrap items-center gap-4">
      <label class="flex items-center gap-2 text-xs text-slate-200">
        <input
          type="checkbox"
          :checked="modelValue.reasoning ?? false"
          @change="update('reasoning', ($event.target as HTMLInputElement).checked)"
        />
        Reasoning
      </label>
      <div class="flex flex-wrap items-center gap-3 text-xs text-slate-200">
        <span class="text-slate-400">Input:</span>
        <label v-for="mode in INPUT_MODES" :key="mode" class="flex items-center gap-1">
          <input
            type="checkbox"
            :checked="isChecked(mode)"
            @change="toggleInput(mode, ($event.target as HTMLInputElement).checked)"
          />
          {{ mode }}
        </label>
      </div>
    </div>
  </fieldset>
</template>
