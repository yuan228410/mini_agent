<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  version: string
  model: string
  context_length: number
  prompt_tokens: number
  completion_tokens: number
  system_prompt_tokens: number
  history_count: number
  planMode?: boolean
}>()

const expanded = ref(false)

const usagePct = computed(() => {
  if (!props.context_length) return '0'
  const pct = (props.prompt_tokens / props.context_length) * 100
  if (pct < 1 && pct > 0) return pct.toFixed(1)
  return Math.round(pct).toString()
})
</script>

<template>
  <div
    class="status-bar"
    @mouseenter="expanded = true"
    @mouseleave="expanded = false"
    :class="{ 'status-bar--expanded': expanded }"
  >
    <span class="status-item" :class="{ 'plan-mode': planMode }">{{ planMode ? '📋 计划' : '⚡ 执行' }}</span>
    <span class="status-sep">·</span>
    <span class="status-item">{{ model }}</span>
    <span class="status-sep">·</span>
    <span class="status-item">ctx {{ usagePct }}%</span>
    <span :class="['status-popup', { 'status-popup--show': expanded }]">
      <span class="status-item">v{{ version }}</span>
      <span class="status-sep">·</span>
      <span class="status-item">↑{{ prompt_tokens }} ↓{{ completion_tokens }}</span>
      <span class="status-sep">·</span>
      <span class="status-item">sys {{ system_prompt_tokens }}</span>
      <span class="status-sep">·</span>
      <span class="status-item">msg {{ history_count }}</span>
    </span>
  </div>
</template>

<style scoped>
.status-bar {
  flex-shrink: 0;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 0.35rem;
  padding: 0.25rem 1.5rem;
  border-top: 0.5px solid var(--border-light);
  font-size: 0.72rem;
  font-family: 'JetBrains Mono', monospace;
  color: var(--fg-dim);
  background: var(--bg);
  cursor: default;
}

.status-sep {
  color: var(--border);
}

.status-popup {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  max-width: 0;
  overflow: hidden;
  opacity: 0;
  transition: max-width 0.35s ease, opacity 0.25s ease, margin-left 0.35s ease;
  margin-left: 0;
  white-space: nowrap;
}

.status-popup--show {
  max-width: 500px;
  opacity: 1;
  margin-left: 0.1rem;
}
</style>
