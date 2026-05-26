<script setup lang="ts">
import { ref, computed } from 'vue'

const props = defineProps<{
  tool: { name: string; args: string; result: string; elapsed: number }
}>()

const expanded = ref(false)

function toggle() {
  expanded.value = !expanded.value
}

const argsDisplay = computed(() => {
  try {
    const parsed = JSON.parse(props.tool.args)
    const keys = Object.keys(parsed)
    if (keys.length <= 2) return props.tool.args
    return `{${keys.slice(0, 2).join(', ')}, ...}`
  } catch {
    return props.tool.args.slice(0, 60)
  }
})
</script>

<template>
  <div class="tool-block">
    <div class="tool-line" @click="toggle">
      <span class="tool-icon">🔧</span>
      <span class="tool-name">{{ tool.name }}</span>
      <span class="tool-args">{{ argsDisplay }}</span>
      <span class="tool-badge">✓ {{ tool.elapsed }}s</span>
      <span class="tool-toggle">{{ expanded ? '收起' : '详情' }}</span>
    </div>
    <div v-if="expanded" class="tool-result">
      <pre>{{ tool.result }}</pre>
    </div>
  </div>
</template>

<style scoped>
.tool-block {
  margin: 0.5rem 0;
  padding-left: 0.8rem;
  border-left: 2px dashed var(--tool-line);
  background: var(--bg-tool);
  border-radius: 0 4px 4px 0;
}

.tool-line {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.6rem;
  cursor: pointer;
  user-select: none;
  flex-wrap: wrap;
}

.tool-icon {
  font-size: 0.8rem;
}

.tool-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--fg);
}

.tool-args {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.78rem;
  color: var(--fg-dim);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-badge {
  font-size: 0.72rem;
  color: #4a9;
  margin-left: auto;
}

.tool-toggle {
  font-size: 0.72rem;
  color: var(--accent);
  opacity: 0;
  transition: opacity 0.2s ease;
}

.tool-line:hover .tool-toggle {
  opacity: 1;
}

.tool-result {
  padding: 0.5rem 0.8rem 0.6rem;
  border-top: 0.5px solid var(--border-light);
  animation: fadeInUp 0.25s ease;
}

.tool-result pre {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  line-height: 1.5;
  color: var(--fg-muted);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
