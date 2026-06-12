<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  model: string
  context_length: number
  prompt_tokens: number
  completion_tokens: number
  history_count: number
  planMode?: boolean
}>()

const modelShort = computed(() => {
  const m = props.model || '?'
  if (m.length <= 12) return m
  const parts = m.split(/[-_]/)
  if (parts.length > 1) return parts.slice(0, 2).join('-')
  return m.slice(0, 12)
})

const usagePct = computed(() => {
  if (!props.context_length) return '0'
  const pct = (props.prompt_tokens / props.context_length) * 100
  if (pct < 1 && pct > 0) return pct.toFixed(1)
  return Math.round(pct).toString()
})
</script>

<template>
  <div class="status-bar">
    <span class="status-item" :class="{ 'plan-mode': planMode }">{{ planMode ? '📋 计划' : '⚡ 执行' }}</span>
    <span class="status-sep">·</span>
    <span class="status-item">{{ modelShort }}</span>
    <span class="status-sep">·</span>
    <span class="status-item">ctx {{ usagePct }}%</span>
    <span class="status-sep">·</span>
    <span class="status-item">↑{{ prompt_tokens }} ↓{{ completion_tokens }}</span>
    <span class="status-sep">·</span>
    <span class="status-item">msg {{ history_count }}</span>
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
</style>
