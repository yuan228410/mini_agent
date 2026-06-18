<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  model: string
  context_length: number
  prompt_tokens: number
  completion_tokens: number
  history_count: number
  planState?: string
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

const planLabel = computed(() => {
  switch (props.planState) {
    case 'planning': return 'PLAN 规划'
    case 'awaiting_user': return 'PLAN 待补充'
    case 'awaiting_approval': return 'PLAN 待确认'
    case 'approved': return 'PLAN 已批准'
    case 'executing': return 'PLAN 执行中'
    default: return 'CHAT 对话'
  }
})
const isPlanActive = computed(() => !['idle', 'completed', 'cancelled', 'superseded', undefined].includes(props.planState as any))
</script>

<template>
  <div class="status-bar">
    <span class="status-item" :class="{ 'plan-mode': isPlanActive }">{{ planLabel }}</span>
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
  padding: 0.32rem 1.5rem;
  border-top: 1px solid var(--surface-hairline);
  font-size: 0.7rem;
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--fg-dim);
  background: linear-gradient(180deg, color-mix(in srgb, var(--bg-card) 44%, transparent), color-mix(in srgb, var(--bg) 82%, transparent));
  backdrop-filter: blur(12px);
  cursor: default;
}

.status-sep {
  color: var(--border);
}

.plan-mode {
  color: var(--accent);
}
</style>
