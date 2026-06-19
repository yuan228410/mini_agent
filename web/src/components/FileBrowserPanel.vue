<script setup lang="ts">
import { ref, nextTick, watch, onMounted, onUnmounted, computed } from 'vue'
import hljs from '../highlight'
import type {
  BreadcrumbItem,
  FileItem,
  FileListResponse,
  FileReadResponse,
  FileSearchResponse,
  WorkspaceMutationResponse,
} from '../api'

const props = defineProps<{ visible?: boolean, embedded?: boolean; workspace: string }>()
const emit = defineEmits(['close', 'workspace-created'])

const items = ref<FileItem[]>([])
const breadcrumb = ref<BreadcrumbItem[]>([])
const currentPath = ref('')
const rootPath = ref('')
const panelWidth = ref(480)
const isResizing = ref(false)
const dirLoading = ref(false)

// ── 预览状态 ──
const previewFile = ref<string | null>(null)
const previewLang = ref('')
const previewContent = ref('')
const previewOffset = ref(0)
const previewHasMore = ref(false)
const previewLoading = ref(false)
const previewTotalLines = ref(0)
const previewSize = ref(0)
const previewModified = ref('')
const previewIsBinary = ref(false)
const previewIsImage = ref(false)
const previewMimeType = ref('')
const codeContainer = ref<HTMLElement | null>(null)
const previewScrollEl = ref<HTMLElement | null>(null)
const showLineNumbers = ref(true)

// ── 键盘导航 ──
const focusedIndex = ref(-1)

// ── 右键菜单 ──
const contextMenu = ref<{ x: number; y: number; path: string; name: string; type: string } | null>(null)
const addWsPending = ref(false)

// ── 设为工作空间 ──
async function setAsWorkspace(fullPath: string, name: string) {
  addWsPending.value = true
  contextMenu.value = null
  try {
    const resp = await fetch('/api/workspaces/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: fullPath, username: _username() }),
    })
    const data = await resp.json() as WorkspaceMutationResponse
    if (data.error) {
      alert(data.error)
      return
    }
    emit('workspace-created', name || fullPath.split('/').pop())
  } catch {
    alert('添加工作空间失败')
  } finally {
    addWsPending.value = false
  }
}

// ── 文件内搜索 ──
const searchQuery = ref('')
const searchMatches = ref<number[]>([])
const currentMatchIndex = ref(0)
const showSearch = ref(false)

// ── 搜索文件 ──
const fileSearchQuery = ref('')
const fileSearchResults = ref<FileItem[]>([])
const showFileSearch = ref(false)
const fileSearchLoading = ref(false)

watch(() => props.visible, (v) => {
  if (v) loadDir('')
})

watch(() => props.workspace, () => {
  loadDir('')
})

onMounted(() => {
  if (props.embedded && props.workspace) loadDir('')
})

watch(() => props.embedded, (v) => {
  if (v && props.workspace) loadDir('')
})

function _username() {
  return localStorage.getItem('mini-ai-username') || 'default'
}

async function loadDir(path: string) {
  previewFile.value = null
  currentPath.value = path
  dirLoading.value = true
  focusedIndex.value = -1
  showFileSearch.value = false
  try {
    const resp = await fetch(`/api/files/list?path=${encodeURIComponent(path)}&workspace=${encodeURIComponent(props.workspace)}&username=${encodeURIComponent(_username())}`)
    const data = await resp.json() as FileListResponse
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
  } finally {
    dirLoading.value = false
  }
}

async function onItemClick(item: FileItem) {
  if (item.type === 'dir') {
    loadDir(item.path)
  } else {
    await openPreview(item)
  }
}

async function openPreview(item: FileItem) {
  previewFile.value = item.path
  previewLang.value = item.language || ''
  previewContent.value = ''
  previewOffset.value = 0
  previewHasMore.value = false
  previewTotalLines.value = 0
  previewSize.value = item.size || 0
  previewModified.value = item.modified || ''
  previewIsBinary.value = false
  previewIsImage.value = false
  previewMimeType.value = ''
  searchQuery.value = ''
  searchMatches.value = []
  showSearch.value = false
  await loadMoreContent()
}

async function loadMoreContent() {
  if (previewLoading.value) return
  previewLoading.value = true
  try {
    const resp = await fetch(
      `/api/files/read?path=${encodeURIComponent(previewFile.value!)}&workspace=${encodeURIComponent(props.workspace)}&offset=${previewOffset.value}&limit=200&username=${encodeURIComponent(_username())}`
    )
    const data = await resp.json() as FileReadResponse
    if (data.error) {
      previewContent.value += `\n// Error: ${data.error}`
      return
    }

    if (data.is_binary) {
      previewIsBinary.value = true
      previewIsImage.value = data.is_image || false
      previewMimeType.value = data.mime_type || ''
      previewSize.value = data.size || 0
      previewModified.value = data.modified || ''
      return
    }

    previewSize.value = data.size || 0
    previewModified.value = data.modified || ''

    const newContent = data.content || ''
    const oldLen = previewContent.value.length
    previewContent.value += newContent
    previewOffset.value = (data.offset || 0) + (data.limit || 0)
    previewHasMore.value = data.has_more || false
    previewTotalLines.value = data.total_lines || 0
    await nextTick()
    highlightIncremental(oldLen)
  } catch {} finally {
    previewLoading.value = false
  }
}

// ── 将 hljs 高亮后的 HTML 按 \n 拆成行数组 ──
function _splitHighlighted(highlightedHtml: string): string[] {
  const fragment = document.createRange().createContextualFragment(highlightedHtml)
  const rows: string[] = []
  let currentLine = ''
  function walkNodes(nodes: NodeList) {
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i]
      if (node.nodeType === Node.TEXT_NODE) {
        const text = node.textContent || ''
        const parts = text.split('\n')
        currentLine += parts[0]
        for (let j = 1; j < parts.length; j++) {
          rows.push(currentLine)
          currentLine = ''
          currentLine += parts[j]
        }
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const el = node as HTMLElement
        const tagName = el.tagName.toLowerCase()
        const attrStr = Array.from(el.attributes).map(a => `${a.name}="${a.value}"`).join(' ')
        currentLine += `<${tagName}${attrStr ? ' ' + attrStr : ''}>`
        walkNodes(el.childNodes)
        currentLine += `</${tagName}>`
      }
    }
  }
  walkNodes(fragment.childNodes)
  if (currentLine || rows.length === 0) rows.push(currentLine || '')
  return rows
}

function highlightIncremental(startOffset: number) {
  if (!codeContainer.value) return
  const pre = codeContainer.value.querySelector('pre')
  if (!pre) return

  const content = previewContent.value
  const lang = previewLang.value
  if (!content) return

  // 临时 <code> 全量高亮（不在 DOM 中，性能开销小）
  const tempCode = document.createElement('code')
  tempCode.textContent = content
  if (lang && hljs.getLanguage(lang)) {
    tempCode.className = `language-${lang}`
    hljs.highlightElement(tempCode)
  } else if (content.trim()) {
    try {
      const result = hljs.highlightAuto(content)
      tempCode.innerHTML = result.value
    } catch {}
  }

  const highlightedRows = _splitHighlighted(tempCode.innerHTML)
  const existingCount = pre.querySelectorAll('.fb-code-row').length

  if (existingCount === 0) {
    // 首次构建：一次性 innerHTML
    pre.innerHTML = highlightedRows.map((html, i) =>
      `<div class="fb-code-row"><span class="fb-line-num">${i + 1}</span><code>${html || '&nbsp;'}</code></div>`
    ).join('')
  } else if (highlightedRows.length > existingCount) {
    // 增量：只追加新行到 DOM，避免全量 innerHTML 重建
    const newRowsHtml = highlightedRows.slice(existingCount).map((html, i) =>
      `<div class="fb-code-row"><span class="fb-line-num">${existingCount + i + 1}</span><code>${html || '&nbsp;'}</code></div>`
    ).join('')
    pre.insertAdjacentHTML('beforeend', newRowsHtml)
  }
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// ── 复制时去掉行号 ──
function onCopy(e: ClipboardEvent) {
  if (!showLineNumbers.value) return // 行号已隐藏时无需处理
  const sel = window.getSelection()
  if (!sel || !sel.rangeCount) return
  const container = previewScrollEl.value
  if (!container || !container.contains(sel.anchorNode)) return

  const temp = document.createElement('div')
  temp.appendChild(sel.getRangeAt(0).cloneContents())
  temp.querySelectorAll('.fb-line-num').forEach(el => el.remove())
  // cloneContents 保留行结构（每行一个 div），逐子节点取文本拼接
  const lines: string[] = []
  let prevEndedWithNewline = true
  for (const child of temp.childNodes) {
    if (child.nodeType === 1) {
      lines.push((child as HTMLElement).innerText || '')
      prevEndedWithNewline = true
    } else if (child.nodeType === 3) {
      const t = child.textContent || ''
      if (t.trim()) {
        if (!prevEndedWithNewline) lines[lines.length - 1] += t
        else lines.push(t)
      }
      prevEndedWithNewline = false
    }
  }
  const text = lines.join('\n').trim()
  if (text) {
    e.clipboardData?.setData('text/plain', text)
    e.preventDefault()
  }
}

// ── 滚动节流 ──
let _scrollTimer: number | null = null
function onScroll(_e: Event) {
  if (_scrollTimer !== null) return
  _scrollTimer = window.setTimeout(() => {
    _scrollTimer = null
    if (!previewHasMore.value || previewLoading.value) return
    const el = previewScrollEl.value
    if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 50) {
      loadMoreContent()
    }
  }, 50)
}

// ── 键盘导航 ──
function onKeydown(e: KeyboardEvent) {
  // 右键菜单打开时不处理
  if (contextMenu.value) {
    if (e.key === 'Escape') contextMenu.value = null
    return
  }
  // 文件搜索框打开时不处理
  if (showFileSearch.value && (e.target as HTMLElement)?.tagName === 'INPUT') return

  if (previewFile.value) {
    // 预览模式
    if (e.key === 'Escape') {
      previewFile.value = null
      e.preventDefault()
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      showSearch.value = !showSearch.value
      if (showSearch.value) {
        nextTick(() => (document.querySelector('.fb-search-input') as HTMLInputElement)?.focus())
      }
      e.preventDefault()
    }
    return
  }

  // 文件列表模式
  if (e.key === 'Escape' && currentPath.value) {
    goBack()
    e.preventDefault()
    return
  }
  if (e.key === 'ArrowDown') {
    focusedIndex.value = Math.min(focusedIndex.value + 1, items.value.length - 1)
    e.preventDefault()
  }
  if (e.key === 'ArrowUp') {
    focusedIndex.value = Math.max(focusedIndex.value - 1, 0)
    e.preventDefault()
  }
  if (e.key === 'Enter' && focusedIndex.value >= 0) {
    onItemClick(items.value[focusedIndex.value])
    e.preventDefault()
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    showFileSearch.value = !showFileSearch.value
    if (showFileSearch.value) {
      nextTick(() => (document.querySelector('.fb-file-search-input') as HTMLInputElement)?.focus())
    }
    e.preventDefault()
  }
}

// ── 右键菜单 ──
function onContextMenu(e: MouseEvent, item: FileItem) {
  e.preventDefault()
  contextMenu.value = { x: e.clientX, y: e.clientY, path: item.path, name: item.name, type: item.type }
}

function closeContextMenu() {
  contextMenu.value = null
}

function copyPath() {
  if (!contextMenu.value) return
  navigator.clipboard.writeText(contextMenu.value.path).catch(() => {})
  contextMenu.value = null
}

function downloadFile() {
  if (!contextMenu.value) return
  const url = `/api/files/raw?path=${encodeURIComponent(contextMenu.value.path)}&workspace=${encodeURIComponent(props.workspace)}&username=${encodeURIComponent(_username())}`
  const a = document.createElement('a')
  a.href = url
  a.download = contextMenu.value.name
  a.click()
  contextMenu.value = null
}

// ── 文件内搜索 ──
function doSearch() {
  if (!searchQuery.value || !previewContent.value) {
    searchMatches.value = []
    return
  }
  const text = previewContent.value
  const query = searchQuery.value.toLowerCase()
  const matches: number[] = []
  let idx = 0
  for (const line of text.split('\n')) {
    if (line.toLowerCase().includes(query)) {
      matches.push(idx + 1)
    }
    idx++
  }
  searchMatches.value = matches
  currentMatchIndex.value = 0
  if (matches.length > 0) {
    scrollToLine(matches[0])
  }
}

function nextMatch() {
  if (searchMatches.value.length === 0) return
  currentMatchIndex.value = (currentMatchIndex.value + 1) % searchMatches.value.length
  scrollToLine(searchMatches.value[currentMatchIndex.value])
}

function prevMatch() {
  if (searchMatches.value.length === 0) return
  currentMatchIndex.value = (currentMatchIndex.value - 1 + searchMatches.value.length) % searchMatches.value.length
  scrollToLine(searchMatches.value[currentMatchIndex.value])
}

function scrollToLine(lineNum: number) {
  if (!codeContainer.value) return
  const rows = codeContainer.value.querySelectorAll('.fb-code-row')
  if (rows.length >= lineNum && rows[lineNum - 1]) {
    rows[lineNum - 1].scrollIntoView({ block: 'center', behavior: 'smooth' })
    // 高亮当前匹配行
    rows.forEach(r => r.classList.remove('fb-search-highlight'))
    rows[lineNum - 1].classList.add('fb-search-highlight')
  }
}

// ── 文件搜索 ──
let _fileSearchTimer: number | null = null
function onFileSearchInput() {
  if (_fileSearchTimer) clearTimeout(_fileSearchTimer)
  _fileSearchTimer = window.setTimeout(async () => {
    if (!fileSearchQuery.value.trim()) {
      fileSearchResults.value = []
      return
    }
    fileSearchLoading.value = true
    try {
      const resp = await fetch(
        `/api/files/search?query=${encodeURIComponent(fileSearchQuery.value)}&path=${encodeURIComponent(currentPath.value)}&workspace=${encodeURIComponent(props.workspace)}&username=${encodeURIComponent(_username())}`
      )
      const data = await resp.json() as FileSearchResponse
      fileSearchResults.value = data.results || []
    } catch {} finally {
      fileSearchLoading.value = false
    }
  }, 300)
}

async function onFileSearchResultClick(item: FileItem) {
  showFileSearch.value = false
  fileSearchQuery.value = ''
  fileSearchResults.value = []
  if (item.type === 'dir') {
    loadDir(item.path)
  } else {
    // 先切到目录再打开
    const parentPath = item.path.split('/').slice(0, -1).join('/')
    if (parentPath && parentPath !== currentPath.value) {
      loadDir(parentPath)
      // 不好直接打开，先设预览
      previewFile.value = item.path
      previewLang.value = item.language || ''
      previewContent.value = ''
      previewOffset.value = 0
      previewHasMore.value = false
      previewTotalLines.value = 0
      previewSize.value = item.size || 0
      previewModified.value = item.modified || ''
      previewIsBinary.value = false
      previewIsImage.value = false
      previewMimeType.value = ''
      searchQuery.value = ''
      searchMatches.value = []
      showSearch.value = false
      await loadMoreContent()
    }
  }
}

// ── 全局点击关闭右键菜单 ──
function onGlobalClick() {
  closeContextMenu()
}

onMounted(() => {
  document.addEventListener('click', onGlobalClick)
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  document.removeEventListener('click', onGlobalClick)
  document.removeEventListener('keydown', onKeydown)
  if (_scrollTimer !== null) clearTimeout(_scrollTimer)
  if (_fileSearchTimer) clearTimeout(_fileSearchTimer)
})

function goBack() {
  if (previewFile.value) {
    previewFile.value = null
    searchQuery.value = ''
    searchMatches.value = []
    showSearch.value = false
    return
  }
  const parts = currentPath.value.split('/').filter(Boolean)
  parts.pop()
  loadDir(parts.join('/'))
}

function goRoot() {
  loadDir('')
}

function goToBreadcrumb(bc: BreadcrumbItem) {
  loadDir(bc.path)
}

function formatSize(bytes?: number): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

function formatModified(iso?: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch { return '' }
}

function fileIcon(item: FileItem): string {
  if (item.type === 'dir') return '📁'
  const ext = item.name.split('.').pop()?.toLowerCase()
  const icons: Record<string, string> = {
    py: '🐍', js: '📜', ts: '📘', vue: '💚', html: '🌐',
    css: '🎨', json: '📋', yaml: '⚙', yml: '⚙', md: '📝',
    sh: '💻', rs: '🦀', go: '🐹', png: '🖼', jpg: '🖼',
    jpeg: '🖼', gif: '🖼', svg: '🖼', ico: '🖼', webp: '🖼',
    pdf: '📕', zip: '📦', gz: '📦', tar: '📦',
  }
  return icons[ext || ''] || '📄'
}

function rawUrl(path: string): string {
  return `/api/files/raw?path=${encodeURIComponent(path)}&workspace=${encodeURIComponent(props.workspace)}&username=${encodeURIComponent(_username())}`
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
<Teleport to="body" :disabled="!!embedded">
    <div v-if="visible || embedded" :class="[embedded ? 'fb-overlay-embedded' : 'fb-overlay']" @click="embedded ? null : emit('close')">
      <div :class="[embedded ? 'fb-embedded-inner' : 'fb-panel']" @click.stop :style="embedded ? {} : { width: panelWidth + 'px' }" tabindex="0">

        <div class="fb-resize-handle" @mousedown.prevent="startResize"></div>
        <div class="fb-header">
          <h3 class="fb-title">文件</h3>
          <div class="fb-header-actions">
            <button class="fb-header-btn" @click="showFileSearch = !showFileSearch" title="搜索文件 (Ctrl+F)">🔍</button>
            <button class="fb-close" @click="emit('close')">✕</button>
          </div>
        </div>

        <!-- 文件搜索 -->
        <div v-if="showFileSearch" class="fb-file-search-bar">
          <input
            v-model="fileSearchQuery"
            class="fb-file-search-input"
            placeholder="搜索文件名..."
            @input="onFileSearchInput"
            @keydown.escape="showFileSearch = false; fileSearchQuery = ''; fileSearchResults = []"
          />
          <span v-if="fileSearchLoading" class="fb-file-search-status">搜索中...</span>
          <span v-else-if="fileSearchQuery" class="fb-file-search-status">{{ fileSearchResults.length }} 个结果</span>
        </div>

        <!-- 文件搜索结果 -->
        <div v-if="showFileSearch && fileSearchResults.length > 0" class="fb-file-search-results">
          <div
            v-for="item in fileSearchResults"
            :key="item.path"
            class="fb-item"
            @click="onFileSearchResultClick(item)"
          >
            <span class="fb-item-icon">{{ fileIcon(item) }}</span>
            <span class="fb-item-name">{{ item.path }}</span>
            <span v-if="item.type === 'file'" class="fb-item-size">{{ formatSize(item.size) }}</span>
          </div>
        </div>

        <!-- Breadcrumb -->
        <div class="fb-breadcrumb">
          <span class="fb-bc-item" @click="goRoot">~</span>
          <template v-for="bc in breadcrumb" :key="bc.path">
            <span class="fb-bc-sep">/</span>
            <span class="fb-bc-item" @click="goToBreadcrumb(bc)">{{ bc.name }}</span>
          </template>
        </div>

        <!-- Loading 骨架 -->
        <div v-if="dirLoading" class="fb-loading-bar">
          <div class="fb-skeleton" v-for="n in 5" :key="n"></div>
        </div>

        <!-- File list -->
        <div v-else-if="!previewFile" class="fb-list">
          <div v-if="currentPath" class="fb-item fb-item-back" @click="goBack">
            <span>⬆ ..</span>
          </div>
          <div
            v-for="(item, idx) in items"
            :key="item.path"
            class="fb-item"
            :class="{ 'fb-item-focused': idx === focusedIndex }"
            @click="onItemClick(item)"
            @contextmenu="onContextMenu($event, item)"
          >
            <span class="fb-item-icon">{{ fileIcon(item) }}</span>
            <span class="fb-item-name">{{ item.name }}</span>
            <span v-if="item.modified" class="fb-item-modified">{{ formatModified(item.modified) }}</span>
            <span v-if="item.type === 'file'" class="fb-item-size">{{ formatSize(item.size) }}</span>
          </div>
          <div v-if="items.length === 0" class="fb-empty">目录为空</div>
        </div>

        <!-- File preview -->
        <div v-else class="fb-preview">
          <div class="fb-preview-header">
            <button class="fb-btn-back" @click="goBack">← 返回</button>
            <span class="fb-preview-name">{{ previewFile.split('/').pop() }}</span>
            <span class="fb-preview-meta">
              <template v-if="previewTotalLines">{{ previewTotalLines }} 行</template>
              <template v-if="previewSize"> · {{ formatSize(previewSize) }}</template>
              <template v-if="previewModified"> · {{ formatModified(previewModified) }}</template>
            </span>
            <button class="fb-btn-toggle-lines" @click="showLineNumbers = !showLineNumbers" :title="showLineNumbers ? '隐藏行号' : '显示行号'">
              <span v-if="showLineNumbers">#</span><span v-else class="fb-lines-off">#</span>
            </button>
          </div>

          <!-- 文件内搜索 -->
          <div v-if="showSearch" class="fb-search-bar">
            <input
              v-model="searchQuery"
              class="fb-search-input"
              placeholder="搜索..."
              @input="doSearch"
              @keydown.enter="nextMatch"
              @keydown.escape="showSearch = false; searchQuery = ''; searchMatches = []"
            />
            <span v-if="searchMatches.length > 0" class="fb-search-info">
              {{ currentMatchIndex + 1 }} / {{ searchMatches.length }}
            </span>
            <button class="fb-search-btn" @click="prevMatch" :disabled="searchMatches.length === 0">↑</button>
            <button class="fb-search-btn" @click="nextMatch" :disabled="searchMatches.length === 0">↓</button>
            <button class="fb-search-btn" @click="showSearch = false; searchQuery = ''; searchMatches = []">✕</button>
          </div>

          <!-- 图片预览 -->
          <div v-if="previewIsImage" class="fb-image-wrap">
            <img :src="rawUrl(previewFile!)" :alt="previewFile!" class="fb-image-preview" />
          </div>
          <!-- 二进制文件 -->
          <div v-else-if="previewIsBinary" class="fb-binary-placeholder">
            <div class="fb-binary-icon">📦</div>
            <div class="fb-binary-text">二进制文件</div>
            <div class="fb-binary-size">{{ formatSize(previewSize) }}</div>
            <a :href="rawUrl(previewFile!)" download class="fb-binary-download">⬇ 下载</a>
          </div>
          <!-- 代码预览 -->
          <div v-else ref="previewScrollEl" class="fb-preview-scroll" :class="{ 'fb-hide-lines': !showLineNumbers }" @scroll="onScroll" @copy="onCopy">
            <div ref="codeContainer" class="fb-code-wrap">
              <pre></pre>
            </div>
            <div v-if="previewLoading" class="fb-loading">加载中...</div>
            <div v-if="previewHasMore && !previewLoading" class="fb-loading">↓ 滚动加载更多</div>
          </div>
        </div>
      
      </div>
    </div>
  </Teleport>

  <!-- 右键菜单 -->
  <Teleport to="body">
    <div v-if="contextMenu" class="fb-context-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }" @click.stop>
      <div class="fb-context-item" @click="copyPath">📋 复制路径</div>
      <div class="fb-context-item" @click="downloadFile" v-if="contextMenu.type === 'file'">⬇ 下载文件</div>
      <div v-if="contextMenu.type === 'dir' && !addWsPending" class="fb-context-item" @click="setAsWorkspace(rootPath + '/' + contextMenu.path, contextMenu.name)">📂 设为工作空间</div>
      <div v-if="addWsPending" class="fb-context-item fb-context-disabled">⏳ 添加中…</div>
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
  outline: none;
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

.fb-header-actions {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.fb-header-btn {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  color: var(--fg-dim);
  font-size: 0.9rem;
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.fb-header-btn:hover { background: var(--bg-card); color: var(--fg); }

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
.fb-item-focused { background: var(--bg-thinking); outline: 1px solid var(--accent); outline-offset: -1px; }

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

.fb-item-modified {
  font-size: 0.68rem;
  color: var(--fg-dim);
  flex-shrink: 0;
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

.fb-loading-bar {
  padding: 0.5rem 1.2rem;
}

.fb-skeleton {
  height: 2rem;
  margin: 0.3rem 0;
  background: var(--bg-card);
  border-radius: 4px;
  animation: skeletonPulse 1.5s ease-in-out infinite;
}

@keyframes skeletonPulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.8; }
}

/* ── 文件搜索 ── */
.fb-file-search-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 1rem;
  border-bottom: 0.5px solid var(--border-light);
}

.fb-file-search-input {
  flex: 1;
  border: 0.5px solid var(--border);
  border-radius: 4px;
  padding: 0.3rem 0.5rem;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  background: var(--bg-card);
  color: var(--fg);
  outline: none;
}

.fb-file-search-input:focus {
  border-color: var(--accent);
}

.fb-file-search-status {
  font-size: 0.7rem;
  color: var(--fg-dim);
  flex-shrink: 0;
}

.fb-file-search-results {
  max-height: 200px;
  overflow-y: auto;
  border-bottom: 0.5px solid var(--border-light);
}

/* ── 预览 ── */
.fb-preview {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.fb-preview-scroll {
  flex: 1;
  overflow-y: auto;
}
.fb-preview-scroll.fb-hide-lines :deep(.fb-line-num) {
  display: none;
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

.fb-btn-toggle-lines {
  background: none;
  border: 1px solid var(--border-light);
  border-radius: 4px;
  color: var(--fg-dim);
  cursor: pointer;
  font-size: 0.75rem;
  line-height: 1;
  margin-left: auto;
  padding: 2px 6px;
  font-family: monospace;
}
.fb-btn-toggle-lines:hover {
  background: var(--bg-card);
  color: var(--fg);
}
.fb-lines-off {
  opacity: 0.4;
}

.fb-preview-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--fg);
}

.fb-preview-meta {
  font-size: 0.7rem;
  color: var(--fg-dim);
  margin-left: auto;
}

/* ── 文件内搜索 ── */
.fb-search-bar {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 0.8rem;
  border-bottom: 0.5px solid var(--border-light);
  flex-shrink: 0;
}

.fb-search-input {
  flex: 1;
  border: 0.5px solid var(--border);
  border-radius: 4px;
  padding: 0.2rem 0.5rem;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  background: var(--bg-card);
  color: var(--fg);
  outline: none;
}

.fb-search-input:focus {
  border-color: var(--accent);
}

.fb-search-info {
  font-size: 0.7rem;
  color: var(--fg-dim);
  min-width: 3rem;
  text-align: center;
}

.fb-search-btn {
  border: 0.5px solid var(--border);
  border-radius: 3px;
  background: var(--bg-card);
  color: var(--fg-dim);
  font-size: 0.7rem;
  cursor: pointer;
  padding: 0.15rem 0.4rem;
}

.fb-search-btn:hover { color: var(--fg); }
.fb-search-btn:disabled { opacity: 0.3; cursor: default; }

/* ── 代码预览 ── */
.fb-code-wrap {
  padding: 0;
}

.fb-code-wrap pre {
  margin: 0;
  padding: 0.5rem 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.78rem;
  line-height: 1.5;
  tab-size: 4;
  overflow-x: auto;
  counter-reset: line;
}

.fb-code-row {
  display: flex;
  min-height: 1.5em;
}

.fb-code-row:hover {
  background: var(--bg-thinking);
}

.fb-line-num {
  display: inline-block;
  width: 3rem;
  min-width: 3rem;
  padding: 0 0.5rem 0 0.3rem;
  text-align: right;
  color: var(--fg-dim);
  font-size: 0.7rem;
  user-select: none;
  flex-shrink: 0;
  border-right: 0.5px solid var(--border-light);
  margin-right: 0.5rem;
}

.fb-code-row code {
  background: transparent !important;
  padding: 0 !important;
  white-space: pre;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.78rem;
  line-height: 1.5;
}

.fb-search-highlight {
  background: rgba(255, 200, 0, 0.25) !important;
}

/* ── 图片预览 ── */
.fb-image-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  padding: 1rem;
}

.fb-image-preview {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 4px;
}

/* ── 二进制占位 ── */
.fb-binary-placeholder {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  color: var(--fg-dim);
}

.fb-binary-icon {
  font-size: 3rem;
}

.fb-binary-text {
  font-size: 1rem;
  font-weight: 500;
}

.fb-binary-size {
  font-size: 0.8rem;
}

.fb-binary-download {
  margin-top: 0.5rem;
  padding: 0.4rem 1rem;
  border: 0.5px solid var(--border);
  border-radius: 4px;
  color: var(--fg);
  text-decoration: none;
  font-size: 0.85rem;
  transition: background 0.15s;
}

.fb-binary-download:hover {
  background: var(--bg-card);
}

.fb-loading {
  text-align: center;
  padding: 0.5rem;
  font-size: 0.75rem;
  color: var(--fg-dim);
}

/* ── 右键菜单 ── */
.fb-context-menu {
  position: fixed;
  z-index: 1000;
  background: var(--bg);
  border: 0.5px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 4px 16px var(--shadow);
  padding: 0.3rem 0;
  min-width: 140px;
  animation: fadeIn 0.1s ease;
}

.fb-context-item {
  padding: 0.4rem 1rem;
  font-size: 0.8rem;
  color: var(--fg);
  cursor: pointer;
  transition: background 0.1s;
}

.fb-context-item:hover {
  background: var(--bg-thinking);
}

.fb-context-disabled {
  opacity: 0.5;
  cursor: default;
}
.fb-context-disabled:hover {
  background: transparent;
}

/* ── Embedded 模式 ── */
.fb-embedded .fb-resize-handle { display: none; }
.fb-embedded-wrap {
  position: static !important;
  background: transparent !important;
  inset: auto !important;
  z-index: auto !important;
  display: flex !important;
  height: 100% !important;
  align-items: stretch !important;
  justify-content: flex-end !important;
}
.fb-embedded-inner {
  position: static !important;
  width: 100% !important;
  height: 100% !important;
  max-height: 100% !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  display: flex !important;
  flex-direction: column !important;
  overflow: hidden !important;
  outline: none;
}
.fb-embedded-inner .fb-resize-handle {
  display: none;
}

.fb-overlay-embedded {
    position: static !important;
    background: transparent !important;
    inset: auto !important;
    z-index: auto !important;
    display: flex !important;
    flex-direction: column !important;
    height: 100% !important;
    pointer-events: auto !important;
}
.fb-panel-embedded {
    position: static !important;
    width: 100% !important;
    height: 100% !important;
    max-height: 100% !important;
    border-radius: 0 !important;
    box-shadow: none !important;
    display: flex !important;
    flex-direction: column !important;
    overflow: hidden !important;
}
.fb-panel-embedded .fb-header,
.fb-panel-embedded .fb-close {
    display: none;
}
</style>