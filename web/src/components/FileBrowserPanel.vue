<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import hljs from 'highlight.js'

interface FileItem {
  name: string
  type: 'dir' | 'file'
  path: string
  size?: number
  language?: string
}

interface Breadcrumb {
  name: string
  path: string
}

const props = defineProps<{ visible: boolean; workspace: string }>()
const emit = defineEmits(['close'])

const items = ref<FileItem[]>([])
const breadcrumb = ref<Breadcrumb[]>([])
const currentPath = ref('')
const rootPath = ref('')
const panelWidth = ref(480)
const isResizing = ref(false)

const previewFile = ref<string | null>(null)
const previewLang = ref('')
const previewContent = ref('')
const previewOffset = ref(0)
const previewHasMore = ref(false)
const previewLoading = ref(false)
const previewTotalLines = ref(0)
const codeContainer = ref<HTMLElement | null>(null)

watch(() => props.visible, (v) => {
  if (v) loadDir('')
})

watch(() => props.workspace, () => {
  loadDir('')
})

async function loadDir(path: string) {
  previewFile.value = null
  currentPath.value = path
  try {
    const resp = await fetch(`/api/files/list?path=${encodeURIComponent(path)}&workspace=${encodeURIComponent(props.workspace)}`)
    const data = await resp.json()
    if (data.error) {
      items.value = []
      breadcrumb.value = []
      rootPath.value = data.error
      return
    }
    items.value = data.items || []
    breadcrumb.value = data.breadcrumb || []
    rootPath.value = data.root || ''
  } catch {
    items.value = []
    breadcrumb.value = []
  }
}

async function onItemClick(item: FileItem) {
  if (item.type === 'dir') {
    loadDir(item.path)
  } else {
    previewFile.value = item.path
    previewLang.value = item.language || ''
    previewContent.value = ''
    previewOffset.value = 0
    previewHasMore.value = false
    previewTotalLines.value = 0
    await loadMoreContent()
  }
}

async function loadMoreContent() {
  if (previewLoading.value) return
  previewLoading.value = true
  try {
    const resp = await fetch(
      `/api/files/read?path=${encodeURIComponent(previewFile.value!)}&workspace=${encodeURIComponent(props.workspace)}&offset=${previewOffset.value}&limit=200`
    )
    const data = await resp.json()
    if (data.error) {
      previewContent.value += `\n// Error: ${data.error}`
      return
    }
    previewContent.value += data.content
    previewOffset.value = data.offset + data.limit
    previewHasMore.value = data.has_more
    previewTotalLines.value = data.total_lines
    await nextTick()
    highlightCode()
  } catch {} finally {
    previewLoading.value = false
  }
}

function highlightCode() {
  if (!codeContainer.value) return
  const el = codeContainer.value.querySelector('code')
  if (el) {
    el.textContent = previewContent.value
    if (previewLang.value && hljs.getLanguage(previewLang.value)) {
      el.className = `language-${previewLang.value}`
    } else {
      el.className = ''
    }
    hljs.highlightElement(el)
  }
}

function onScroll(e: Event) {
  if (!previewHasMore.value || previewLoading.value) return
  const el = e.target as HTMLElement
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 50) {
    loadMoreContent()
  }
}

function goBack() {
  if (previewFile.value) {
    previewFile.value = null
    return
  }
  const parts = currentPath.value.split('/').filter(Boolean)
  parts.pop()
  loadDir(parts.join('/'))
}

function goRoot() {
  loadDir('')
}

function goToBreadcrumb(bc: Breadcrumb) {
  loadDir(bc.path)
}

function formatSize(bytes?: number): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

function fileIcon(item: FileItem): string {
  if (item.type === 'dir') return '📁'
  const ext = item.name.split('.').pop()?.toLowerCase()
  const icons: Record<string, string> = {
    py: '🐍', js: '📜', ts: '📘', vue: '💚', html: '🌐',
    css: '🎨', json: '📋', yaml: '⚙', yml: '⚙', md: '📝',
    sh: '💻', rs: '🦀', go: '🐹',
  }
  return icons[ext || ''] || '📄'
}

function startResize(e: MouseEvent) {
  isResizing.value = true
  const startX = e.clientX
  const startWidth = panelWidth.value

  function onMove(ev: MouseEvent) {
    const delta = startX - ev.clientX
    panelWidth.value = Math.max(320, Math.min(window.innerWidth * 0.8, startWidth + delta))
  }

  function onUp() {
    isResizing.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }

  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="fb-overlay" @click="emit('close')">
      <div class="fb-panel" :style="{ width: panelWidth + 'px' }" @click.stop>
        <div class="fb-resize-handle" @mousedown.prevent="startResize"></div>
        <div class="fb-header">
          <h3 class="fb-title">文件</h3>
          <button class="fb-close" @click="emit('close')">✕</button>
        </div>

        <!-- Breadcrumb -->
        <div class="fb-breadcrumb">
          <span class="fb-bc-item" @click="goRoot">~</span>
          <template v-for="bc in breadcrumb" :key="bc.path">
            <span class="fb-bc-sep">/</span>
            <span class="fb-bc-item" @click="goToBreadcrumb(bc)">{{ bc.name }}</span>
          </template>
        </div>

        <!-- File list -->
        <div v-if="!previewFile" class="fb-list">
          <div v-if="currentPath" class="fb-item fb-item-back" @click="goBack">
            <span>⬆ ..</span>
          </div>
          <div
            v-for="item in items"
            :key="item.path"
            class="fb-item"
            @click="onItemClick(item)"
          >
            <span class="fb-item-icon">{{ fileIcon(item) }}</span>
            <span class="fb-item-name">{{ item.name }}</span>
            <span v-if="item.type === 'file'" class="fb-item-size">{{ formatSize(item.size) }}</span>
          </div>
          <div v-if="items.length === 0" class="fb-empty">目录为空</div>
        </div>

        <!-- File preview -->
        <div v-else class="fb-preview" @scroll="onScroll">
          <div class="fb-preview-header">
            <button class="fb-btn-back" @click="goBack">← 返回</button>
            <span class="fb-preview-name">{{ previewFile.split('/').pop() }}</span>
            <span class="fb-preview-info">{{ previewTotalLines }} 行</span>
          </div>
          <div ref="codeContainer" class="fb-code-wrap">
            <pre><code>{{ previewContent }}</code></pre>
          </div>
          <div v-if="previewLoading" class="fb-loading">加载中...</div>
          <div v-if="previewHasMore && !previewLoading" class="fb-loading">↓ 滚动加载更多</div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.fb-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  z-index: 200;
  animation: fadeIn 0.2s ease;
}

.fb-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  background: var(--bg);
  border-left: 0.5px solid var(--border);
  box-shadow: -4px 0 20px var(--shadow);
  display: flex;
  flex-direction: column;
  animation: slideIn 0.25s ease;
}

.fb-resize-handle {
  position: absolute;
  top: 0;
  left: -3px;
  width: 6px;
  height: 100%;
  cursor: col-resize;
  z-index: 10;
}

.fb-resize-handle:hover,
.fb-resize-handle:active {
  background: var(--accent, #4a9eff);
  opacity: 0.3;
}

@keyframes slideIn {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.fb-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.2rem;
  border-bottom: 0.5px solid var(--border);
}

.fb-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--fg);
}

.fb-close {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  color: var(--fg-dim);
  font-size: 1rem;
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.fb-close:hover { background: var(--bg-card); color: var(--fg); }

.fb-breadcrumb {
  padding: 0.5rem 1.2rem;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: var(--fg-dim);
  border-bottom: 0.5px solid var(--border-light);
  overflow-x: auto;
  white-space: nowrap;
}

.fb-bc-item {
  cursor: pointer;
  color: var(--fg-dim);
  transition: color 0.2s;
}

.fb-bc-item:hover { color: var(--fg); }

.fb-bc-sep { margin: 0 0.3rem; color: var(--border); }

.fb-list {
  flex: 1;
  overflow-y: auto;
}

.fb-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.55rem 1.2rem;
  cursor: pointer;
  border-bottom: 0.5px solid var(--border-light);
  transition: background 0.15s ease;
}

.fb-item:hover { background: var(--bg-thinking); }

.fb-item-back { color: var(--fg-dim); font-size: 0.85rem; }

.fb-item-icon { font-size: 0.9rem; flex-shrink: 0; }

.fb-item-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.82rem;
  color: var(--fg);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fb-item-size {
  font-size: 0.7rem;
  color: var(--fg-dim);
  flex-shrink: 0;
}

.fb-empty {
  padding: 2rem;
  text-align: center;
  color: var(--fg-dim);
  font-size: 0.9rem;
}

.fb-preview {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.fb-preview-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  border-bottom: 0.5px solid var(--border-light);
  flex-shrink: 0;
}

.fb-btn-back {
  border: none;
  background: none;
  color: var(--fg-dim);
  font-size: 0.8rem;
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  border-radius: 4px;
}

.fb-btn-back:hover { background: var(--bg-card); color: var(--fg); }

.fb-preview-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--fg);
}

.fb-preview-info {
  font-size: 0.7rem;
  color: var(--fg-dim);
  margin-left: auto;
}

.fb-code-wrap {
  flex: 1;
  overflow: auto;
  padding: 0;
}

.fb-code-wrap pre {
  margin: 0;
  padding: 0.8rem 1rem;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.78rem;
  line-height: 1.5;
  tab-size: 4;
  overflow-x: auto;
}

.fb-code-wrap code {
  background: transparent !important;
  padding: 0 !important;
}

.fb-loading {
  text-align: center;
  padding: 0.5rem;
  font-size: 0.75rem;
  color: var(--fg-dim);
}
</style>
