<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { marked } from 'marked'

const props = defineProps<{
  thinking: { chars: number; elapsed: number; content: string }
  teammate?: string
}>()

// 自动折叠阈值
const AUTO_COLLAPSE_LEN = 300

// 用户偏好存储
const STORAGE_KEY = 'mini-ai-thinking-expanded'

// 从 localStorage 读取偏好
const getStoredPreference = (): boolean => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === 'true'
  } catch {
    return false
  }
}

// 初始状态：短内容或用户偏好展开时才展开
const expanded = ref(getStoredPreference() && (props.thinking.chars || 0) <= AUTO_COLLAPSE_LEN)

// 是否需要折叠
const needsCollapse = computed(() => (props.thinking.chars || 0) > AUTO_COLLAPSE_LEN)

const renderedThinking = computed(() => {
  if (!props.thinking.content) return ''
  return marked.parse(props.thinking.content)
})

function toggle() {
  expanded.value = !expanded.value
  // 保存偏好
  try {
    localStorage.setItem(STORAGE_KEY, String(expanded.value))
  } catch {}
}
</script>

<template>
  <div class="thinking-block" :class="{ 'thinking-block--expanded': expanded }">
    <div class="thinking-line" @click="toggle">
      <span class="thinking-marker">💭</span>
      <span class="thinking-summary">
        已思考 <span class="thinking-chars">{{ thinking.chars }}</span> 字
        <span class="thinking-time">({{ thinking.elapsed }}s)</span>
        <span v-if="needsCollapse && !expanded" class="thinking-hint">点击展开</span>
      </span>
      <span class="thinking-toggle">{{ expanded ? '收起' : '展开' }}</span>
    </div>
    <div v-if="expanded" class="thinking-content" :class="{ 'thinking-content--long': needsCollapse }">
      <div v-html="renderedThinking"></div>
    </div>
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

.thinking-block--expanded {
  border-left-width: 3px;
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

.thinking-chars {
  font-weight: 500;
  color: var(--fg);
}

.thinking-time {
  opacity: 0.8;
}

.thinking-hint {
  font-size: 0.72rem;
  color: var(--accent);
  margin-left: 0.3rem;
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

.thinking-content--long {
  max-height: 400px;
  overflow-y: auto;
  position: relative;
}

.thinking-content--long::after {
  content: '';
  position: sticky;
  bottom: 0;
  left: 0;
  right: 0;
  height: 20px;
  background: linear-gradient(transparent, var(--bg-thinking));
  pointer-events: none;
}

.thinking-content :deep(p) {
  margin-bottom: 0.5em;
}

.thinking-content :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85em;
  padding: 0.1em 0.3em;
  background: var(--bg-code);
  border-radius: 3px;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
