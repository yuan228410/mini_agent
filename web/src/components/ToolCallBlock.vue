<script setup lang="ts">
import { ref, computed } from 'vue'

const props = defineProps<{
  tool: { name: string; args: string; result: string; elapsed: number }
}>()

const expanded = ref(false)
const resultExpanded = ref(false)

// 结果长度阈值
const PREVIEW_LEN = 150
const AUTO_EXPAND_LEN = 50

const resultLen = computed(() => (props.tool.result || '').length)
const needsPreview = computed(() => resultLen.value > PREVIEW_LEN)

const resultPreview = computed(() => {
  const r = props.tool.result || ''
  if (r.length <= PREVIEW_LEN) return r
  return r.slice(0, PREVIEW_LEN) + '...'
})

const resultIsJson = computed(() => {
  const r = props.tool.result || ''
  if (!r.trim()) return false
  try {
    JSON.parse(r)
    return true
  } catch {
    return false
  }
})

const formattedResult = computed(() => {
  if (!resultIsJson.value) return props.tool.result || ''
  try {
    return JSON.stringify(JSON.parse(props.tool.result || ''), null, 2)
  } catch {
    return props.tool.result || ''
  }
})

// 检测文件路径（常见格式）
const filePathPattern = /^(\/[\w\-./]+|[A-Za-z]:\\[\w\-./\\]+|~\/[\w\-./]+|\.\/[\w\-./]+|\.\.\/[\w\-./]+)$/

function toggle() {
  expanded.value = !expanded.value
}

function toggleResult() {
  resultExpanded.value = !resultExpanded.value
}
</script>

<template>
  <div class="tool-block">
    <div class="tool-line" @click="toggle">
      <span class="tool-icon">🔧</span>
      <span class="tool-name">{{ tool.name }}</span>
      <span class="tool-badge">✓ {{ tool.elapsed }}s</span>
      <span v-if="resultLen > 0" class="tool-size">{{ resultLen > 1000 ? (resultLen / 1000).toFixed(1) + 'k' : resultLen }} 字</span>
      <span class="tool-toggle">{{ expanded ? '收起' : '详情' }}</span>
    </div>
    <div v-if="expanded" class="tool-args-line" @click="toggle">
      <span class="tool-args">{{ tool.args }}</span>
    </div>
    
    <!-- 结果预览（展开状态） -->
    <div v-if="expanded && tool.result" class="result-preview-line" @click="toggle">
      <span class="result-preview-label">结果:</span>
      <span class="result-preview" :class="{ 'result-json': resultIsJson }">{{ resultPreview }}</span>
      <span v-if="needsPreview" class="result-more">+{{ resultLen - PREVIEW_LEN }} 字</span>
    </div>
    
    <!-- 详情展开 -->
    <div v-if="expanded" class="tool-detail">
      <div class="detail-section">
        <div class="detail-label">参数</div>
        <pre class="detail-content">{{ tool.args }}</pre>
      </div>
      <div v-if="tool.result" class="detail-section">
        <div class="detail-label">
          结果
          <span v-if="resultIsJson" class="result-type-badge">JSON</span>
          <span class="result-size-badge">{{ resultLen }} 字</span>
        </div>
        <div class="result-wrapper">
          <pre class="detail-content" :class="{ 'result-json-content': resultIsJson }">{{ resultExpanded || resultLen <= 500 ? formattedResult : formattedResult.slice(0, 500) + '...' }}</pre>
          <button v-if="resultLen > 500" class="result-expand-btn" @click.stop="toggleResult">
            {{ resultExpanded ? '收起' : `展开全部 (${resultLen} 字)` }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool-block {
  margin: 0.65rem 0;
  padding: 0.12rem 0 0.12rem 0.75rem;
  border: 1px solid color-mix(in srgb, var(--tool-line) 42%, var(--border));
  border-left: 3px dashed var(--tool-line);
  background: linear-gradient(180deg, color-mix(in srgb, var(--bg-tool) 64%, transparent), color-mix(in srgb, var(--bg-card) 46%, transparent));
  border-radius: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.032);
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
  font-family: var(--font-mono);
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--fg);
}

.tool-badge {
  font-size: 0.72rem;
  color: #4a9;
  margin-left: auto;
}

.tool-size {
  font-size: 0.68rem;
  color: var(--fg-dim);
  background: var(--bg-thinking);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
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
  font-family: var(--font-mono);
  font-size: 0.75rem;
  line-height: 1.4;
  color: var(--fg-dim);
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 结果预览 */
.result-preview-line {
  padding: 0.2rem 0.6rem 0.35rem 1.8rem;
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 0.3rem;
}

.result-preview-label {
  font-size: 0.7rem;
  color: var(--fg-dim);
  flex-shrink: 0;
}

.result-preview {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  line-height: 1.4;
  color: var(--fg-muted);
  word-break: break-all;
  flex: 1;
}

.result-preview.result-json {
  color: #9b59b6;
}

.result-more {
  font-size: 0.65rem;
  color: var(--accent);
  background: var(--bg-thinking);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  flex-shrink: 0;
}

.tool-detail {
  padding: 0.5rem 0.8rem 0.6rem;
  border-top: 0.5px solid var(--border-light);
  animation: fadeInUp 0.25s ease;
}

.detail-section {
  margin-bottom: 0.5rem;
}

.detail-label {
  font-size: 0.72rem;
  color: var(--fg-dim);
  margin-bottom: 0.25rem;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.result-type-badge {
  font-size: 0.6rem;
  background: #9b59b6;
  color: #fff;
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
}

.result-size-badge {
  font-size: 0.6rem;
  background: var(--bg-thinking);
  color: var(--fg-dim);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
}

.result-wrapper {
  position: relative;
}

.detail-content {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  line-height: 1.5;
  color: var(--fg-muted);
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  max-height: 400px;
  overflow-y: auto;
}

.detail-content.result-json-content {
  color: #d4d4d4;
  background: #1e1e1e;
  padding: 0.5rem;
  border-radius: 4px;
}

.result-expand-btn {
  display: block;
  width: 100%;
  margin-top: 0.3rem;
  padding: 0.3rem;
  border: 1px dashed var(--border);
  background: var(--bg-thinking);
  color: var(--accent);
  font-size: 0.72rem;
  cursor: pointer;
  border-radius: 4px;
  transition: all 0.2s ease;
}

.result-expand-btn:hover {
  background: var(--bg-card);
  border-color: var(--accent);
}
</style>
