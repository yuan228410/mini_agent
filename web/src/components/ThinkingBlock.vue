<script setup lang="ts">
import { ref, computed } from 'vue'
import { marked } from 'marked'

const props = defineProps<{
  thinking: { chars: number; elapsed: number; content: string }
}>()

const expanded = ref(false)

const renderedThinking = computed(() => {
  if (!props.thinking.content) return ''
  return marked.parse(props.thinking.content)
})

function toggle() {
  expanded.value = !expanded.value
}
</script>

<template>
  <div class="thinking-block" :class="{ 'thinking-block--expanded': expanded }">
    <div class="thinking-line" @click="toggle">
      <span class="thinking-marker">💭</span>
      <span class="thinking-summary">
        已思考 {{ thinking.chars }} 字 ({{ thinking.elapsed }}s)
      </span>
      <span class="thinking-toggle">{{ expanded ? '收起' : '展开' }}</span>
    </div>
    <div v-if="expanded" class="thinking-content" v-html="renderedThinking"></div>
  </div>
</template>

<style scoped>
.thinking-block {
  margin: 0.6rem 0;
  padding-left: 0.8rem;
  border-left: 2px solid var(--thinking-line);
  background: var(--bg-thinking);
  border-radius: 0 4px 4px 0;
  transition: all 0.3s ease;
}

.thinking-line {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.6rem;
  cursor: pointer;
  user-select: none;
}

.thinking-marker {
  font-size: 0.85rem;
}

.thinking-summary {
  font-size: 0.82rem;
  color: var(--fg-muted);
  font-style: italic;
}

.thinking-toggle {
  font-size: 0.75rem;
  color: var(--accent);
  margin-left: auto;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.thinking-line:hover .thinking-toggle {
  opacity: 1;
}

.thinking-content {
  padding: 0.6rem 0.8rem 0.8rem;
  font-size: 0.9rem;
  line-height: 1.65;
  color: var(--fg-muted);
  font-style: italic;
  border-top: 0.5px solid var(--border-light);
  animation: fadeInUp 0.25s ease;
}

.thinking-content :deep(p) {
  margin-bottom: 0.5em;
}
</style>
