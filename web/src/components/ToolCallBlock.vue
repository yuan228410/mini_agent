<script setup lang="ts">
import { ref, computed } from 'vue'

const props = defineProps<{
  tool: { name: string; args: string; result: string; elapsed: number }
}>()

const expanded = ref(false)

function toggle() {
  expanded.value = !expanded.value
}
</script>

<template>
  <div class="tool-block">
    <div class="tool-line" @click="toggle">
      <span class="tool-icon">🔧</span>
      <span class="tool-name">{{ tool.name }}</span>
      <span class="tool-badge">✓ {{ tool.elapsed }}s</span>
      <span class="tool-toggle">{{ expanded ? '收起' : '详情' }}</span>
    </div>
    <div class="tool-args-line" @click="toggle">
      <span class="tool-args">{{ tool.args }}</span>
    </div>
    <div v-if="expanded" class="tool-detail">
      <div class="detail-section">
        <div class="detail-label">参数</div>
        <pre class="detail-content">{{ tool.args }}</pre>
      </div>
      <div v-if="tool.result" class="detail-section">
        <div class="detail-label">结果</div>
        <pre class="detail-content">{{ tool.result }}</pre>
      </div>
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
  padding: 0.35rem 0.6rem 0;
  cursor: pointer;
  user-select: none;
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

.tool-args-line {
  padding: 0.1rem 0.6rem 0.35rem 1.8rem;
  cursor: pointer;
}

.tool-args {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  line-height: 1.4;
  color: var(--fg-dim);
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.tool-detail {
  padding: 0.5rem 0.8rem 0.6rem;
  border-top: 0.5px solid var(--border-light);
  animation: fadeInUp 0.25s ease;
}

.detail-section {
  margin-bottom: 0.4rem;
}

.detail-label {
  font-size: 0.72rem;
  color: var(--fg-dim);
  margin-bottom: 0.2rem;
}

.detail-content {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  line-height: 1.5;
  color: var(--fg-muted);
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
</style>
