<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  model: string
  context_length: number
  prompt_tokens: number
  completion_tokens: number
  system_prompt_chars: number
  history_count: number
}>()

const usagePct = computed(() => {
  if (!props.context_length) return '0'
  const pct = (props.prompt_tokens / props.context_length) * 100
  if (pct < 1 && pct > 0) return pct.toFixed(1)
  return Math.round(pct).toString()
})
</script>

<template>
  <div class="status-bar">
    <span class="status-item">⚙ {{ model }}</span>
    <span class="status-sep">│</span>
    <span class="status-item">ctx {{ usagePct }}% ({{ prompt_tokens }}/{{ context_length }})</span>
    <span class="status-sep">│</span>
    <span class="status-item">↑{{ prompt_tokens }} ↓{{ completion_tokens }}</span>
    <span class="status-sep">│</span>
    <span class="status-item">sys {{ system_prompt_chars }}</span>
    <span class="status-sep">│</span>
    <span class="status-item">msg {{ history_count }}</span>
  </div>
</template>

<style scoped>
.status-bar {
  flex-shrink: 0;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 1.5rem;
  border-top: 0.5px solid var(--border-light);
  font-size: 0.7rem;
  font-family: 'JetBrains Mono', monospace;
  color: var(--fg-dim);
  background: var(--bg);
}

.status-sep {
  color: var(--border);
  margin: 0 0.1rem;
}
</style>
