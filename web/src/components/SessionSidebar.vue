<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue'
import {
  getSessions, createSession, deleteSession, batchDeleteSessions, renameSession,
  exportSession,
  getWorkspaces, createWorkspace, addWorkspace, removeWorkspace, switchWorkspace,
  listRemovedWorkspaces, restoreWorkspace, deleteRemovedWorkspace,
  browseDirs,
  type SessionInfo, type WorkspaceInfo, type BrowseDir,
} from '../api'

const props = defineProps<{ width: number; collapsed: boolean }>()
const emit = defineEmits(['switch-session', 'status-change', 'toggle', 'workspace-change'])

const WORKSPACE_KEY = 'mini-ai-active-workspace'

const workspaces = ref<WorkspaceInfo[]>([])
const activeWorkspace = ref<string | null>(null)
const activeSessionId = ref('')
const editingSid = ref('')
const editingName = ref('')
const contextMenu = ref<{ x: number; y: number; sid: string; ws: string } | null>(null)
const batchMode = ref(false)
const selectedSessions = ref(new Set<string>())

const wsSessions: Record<string, SessionInfo[]> = reactive({})
const wsCollapsed: Record<string, boolean> = reactive({})

const showAddWsPopup = ref(false)
const wsPopupTab = ref<'add' | 'restore'>('add')
const removedWorkspaces = ref<{name: string; project_path: string}[]>([])
const addWsPath = ref('')
const addWsSubmitting = ref(false)
const addWsError = ref('')

const showDirPicker = ref(false)
const dirCurrent = ref('')
const dirParent = ref('')
const dirDirs = ref<BrowseDir[]>([])
const dirLoading = ref(false)

onMounted(() => {
  const savedWs = localStorage.getItem(WORKSPACE_KEY) || 'default'
  activeWorkspace.value = savedWs
  emit('workspace-change', savedWs)
  loadAll().catch(() => {})
})

async function loadAll() {
  try { await loadWorkspaces() } catch {}
  loadAllSessions().catch(() => {})
  loadRemoved().catch(() => {})
}

async function loadWorkspaces() {
  try {
    const resp = await getWorkspaces()
    workspaces.value = resp.workspaces || []
    // 如果 activeWorkspace 不在列表中，切回 default
    if (activeWorkspace.value && !workspaces.value.some((w: any) => w.name === activeWorkspace.value)) {
      activeWorkspace.value = 'default'
      localStorage.setItem(WORKSPACE_KEY, 'default')
      emit('workspace-change', 'default')
    }
  } catch {}
}

function _preserveGenerating(oldList: SessionInfo[], newList: SessionInfo[]): SessionInfo[] {
  const map = new Map<string, string>()
  for (const s of oldList) {
    if (s.status === 'generating') map.set(s.session_id, 'generating')
  }
  for (const s of newList) {
    if (map.has(s.session_id)) s.status = map.get(s.session_id)! as SessionInfo["status"]
  }
  return newList
}

async function loadAllSessions() {
  const promises: Promise<void>[] = []
  promises.push((async () => {

  })())
  for (const ws of workspaces.value) {
    promises.push((async () => {
      try { const resp = await getSessions(ws.name); wsSessions[ws.name] = _preserveGenerating(wsSessions[ws.name] || [], resp.sessions || []) } catch { wsSessions[ws.name] = [] }
    })())
  }
  await Promise.all(promises)
}

async function loadSessionsFor(wsName: string | null) {
  try {
    const resp = await getSessions(wsName || undefined)
    if (wsName) {
      wsSessions[wsName] = _preserveGenerating(wsSessions[wsName] || [], resp.sessions || [])
    } else {

    }
  } catch {}
}

function relativeTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = Date.now()
  const diff = now - d.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`
  return `${d.getMonth() + 1}/${d.getDate()}`
}

function toggleCollapse(wsName: string) {
  wsCollapsed[wsName] = !wsCollapsed[wsName]
}

async function selectSession(sid: string, wsName: string | null) {
  activeSessionId.value = sid
  activeWorkspace.value = wsName
  localStorage.setItem(WORKSPACE_KEY, wsName || '')
  emit('workspace-change', wsName)
  if (wsName) {
    try { await switchWorkspace(wsName) } catch {}
  }
  emit('switch-session', sid, wsName)
}

async function newSessionFor(wsName: string | null) {
  try {
    const resp = await createSession(wsName || undefined)
    if (resp.session_id) {
      await loadSessionsFor(wsName)
      
      // 初始化会话状态（调用 ChatView 的初始化）
      // 通过 switch-session 事件触发 ChatView 初始化
      activeSessionId.value = resp.session_id
      activeWorkspace.value = wsName
      
      // 保存工作空间到 localStorage
      localStorage.setItem(WORKSPACE_KEY, wsName || '')
      
      emit('switch-session', resp.session_id, wsName)
    }
  } catch {}
}

function onContextMenu(e: MouseEvent, sid: string, wsName: string) {
  e.preventDefault()
  contextMenu.value = { x: e.clientX, y: e.clientY, sid, ws: wsName }
}

function closeContextMenu() {
  contextMenu.value = null
}

const _batchWsName = ref('')
const batchTargetWs = ref('default')

function enterBatchMode(wsName: string) {
  if (batchMode.value && batchTargetWs.value === wsName) {
    batchMode.value = false
    selectedSessions.value = new Set()
    return
  }
  batchMode.value = true
  batchTargetWs.value = wsName
  _batchWsName.value = wsName
  selectedSessions.value = new Set()
}

function selectAllForWs() {
  _batchWsName.value = batchTargetWs.value
  doSelectAll()
}

function doSelectAll() {
  const wsName = _batchWsName.value

  const sessions = wsSessions[wsName] || []
  const s = new Set<string>()
  for (const ses of sessions) {
    s.add(ses.session_id)
  }
  selectedSessions.value = s
}

function toggleSelect(sid: string) {
  const s = new Set(selectedSessions.value)
  if (s.has(sid)) { s.delete(sid) } else { s.add(sid) }
  selectedSessions.value = s
}



async function batchDelete() {
  if (selectedSessions.value.size === 0) return
  if (!confirm(`确定删除选中的 ${selectedSessions.value.size} 个会话？`)) return
  const ids = Array.from(selectedSessions.value)
  await batchDeleteSessions(ids, batchTargetWs.value || undefined)
  selectedSessions.value = new Set()
  batchMode.value = false
  batchTargetWs.value = "default"
  await loadAllSessions()
}

async function doDelete(sid: string) {
  const ws = contextMenu.value?.ws || activeWorkspace.value
  contextMenu.value = null
  
  // 通知 ChatView 清理本地状态
  // 通过自定义事件传递删除信号
  window.dispatchEvent(new CustomEvent('session-delete', { detail: { sid, ws } }))
  
  await deleteSession(sid, ws || undefined)
  await loadAllSessions()
  
  if (activeSessionId.value === sid) {
    const all = getAllSessions()
    if (all.length > 0) {
      activeSessionId.value = all[0].session_id
      emit('switch-session', all[0].session_id, null)
    } else {
      activeSessionId.value = ''
    }
  }
}

async function doExport(sid: string) {
  const ws = contextMenu.value?.ws || activeWorkspace.value || ''
  contextMenu.value = null
  const input = prompt('导出参数（条数,是否含思考,是否含工具）\n例: 0,false,false = 全部消息，不含思考和工具', '0,false,false')
  if (input === null) return
  const parts = input.split(',').map(s => s.trim())
  const limit = parseInt(parts[0]) || 0
  const thinking = parts[1] === 'true'
  const tools = parts[2] === 'true'
  try {
    await exportSession(sid, ws || undefined, limit, thinking, tools)
  } catch (e: any) {
    alert(e.message || '导出失败')
  }
}

function startEdit(sid: string, currentName: string) {
  contextMenu.value = null
  editingSid.value = sid
  editingName.value = currentName
}

async function finishEdit(sid: string) {
  const name = editingName.value.trim()
  editingSid.value = ''
  if (!name) return
  await renameSession(sid, name, activeWorkspace.value || undefined)
  await loadAllSessions()
}

function handleEditKey(e: KeyboardEvent, sid: string) {
  if (e.key === 'Enter') finishEdit(sid)
  if (e.key === 'Escape') { editingSid.value = '' }
}

function _inferName(path: string): string {
  return path.split('/').filter(Boolean).pop() || path.split('\\').filter(Boolean).pop() || ''
}

async function openDirPicker() {
  dirCurrent.value = ''
  dirDirs.value = []
  showDirPicker.value = true
  await loadDir('')
}

async function loadDir(path: string) {
  dirLoading.value = true
  try {
    const resp = await browseDirs(path || undefined)
    dirCurrent.value = resp.current
    dirParent.value = resp.parent
    dirDirs.value = resp.dirs || []
  } catch {}
  dirLoading.value = false
}

function selectDir(path: string) {
  addWsPath.value = path
  showDirPicker.value = false
  // 自动提交
  submitAddWs()
}

async function submitAddWs() {
  const path = addWsPath.value.trim()
  if (!path) return
  addWsSubmitting.value = true
  addWsError.value = ''
  const name = _inferName(path)
  if (!name) {
    addWsError.value = '无法从路径推断名称'
    addWsSubmitting.value = false
    return
  }
  try {
    const resp = await createWorkspace(name, path)
    if (resp.error) {
      addWsError.value = resp.error
      addWsSubmitting.value = false
      return
    }
    addWsPath.value = ''
    showAddWsPopup.value = false
    addWsError.value = ''
    await loadAll()
  } catch {
    addWsError.value = '添加失败'
  } finally {
    addWsSubmitting.value = false
  }
}

async function addWsByPath() {
  const path = addWsPath.value.trim()
  if (!path) return
  await submitAddWs()
}

async function loadRemoved() {
  try {
    const resp = await listRemovedWorkspaces()
    removedWorkspaces.value = resp.removed || []
  } catch {}
}

async function restoreWs(name: string) {
  try {
    const resp = await restoreWorkspace(name)
    if (resp.error) { alert(resp.error); return }
    await loadAll()
    await loadRemoved()
  } catch {}
}

async function deleteRemovedWs(name: string) {
  if (!confirm(`确定彻底删除已移除的工作空间 "${name}"？此操作不可恢复！`)) return
  try {
    const resp = await deleteRemovedWorkspace(name)
    if (resp.error) { alert(resp.error); return }
    await loadRemoved()
  } catch {}
}

function confirmDeleteWs(name: string) {
  if (confirm(`确定删除工作空间 "${name}" 及其所有数据？此操作不可恢复！`)) {
    deleteWs(name)
  }
}

async function removeWs(name: string) {
  if (!confirm(`确定移除工作空间 "${name}"？数据将保留，可重新添加。`)) return
  await removeWorkspace(name, false)
  await loadAll()
}

async function deleteWs(name: string) {
  if (!confirm(`确定删除工作空间 "${name}" 及其所有数据？`)) return
  await removeWorkspace(name, true)
  await loadAll()
}

function getAllSessions(): SessionInfo[] {
  const all: SessionInfo[] = []
  for (const ws of workspaces.value) {
    all.push(...(wsSessions[ws.name] || []))
  }

  return all
}

function updateSessionStatus(sid: string, status: 'idle' | 'generating' | 'connected' | 'disconnected') {
  for (const ws of workspaces.value) {
    const s = (wsSessions[ws.name] || []).find(s => s.session_id === sid)
    if (s) { 
      // 只更新 idle/generating 状态
      if (status === 'idle' || status === 'generating') {
        s.status = status
      }
      return
    }
  }
  return null
}


function setActiveSession(sid: string) {
  activeSessionId.value = sid
}

defineExpose({ loadSessions: loadAllSessions, updateSessionStatus, setActiveSession, activeWorkspace })
</script>

<template>
  <div v-if="!collapsed" class="sidebar" :style="{ width: width + 'px' }" @click="closeContextMenu">
    <div class="sidebar-header">
      <span class="sidebar-brand">mini_ai</span>
      <button class="ws-add-ws-btn" @click="showAddWsPopup = !showAddWsPopup; if (showAddWsPopup) { wsPopupTab = 'add'; addWsPath = ''; addWsError = ''; loadRemoved() }" title="添加工作空间">+ 空间</button>
    </div>

    <!-- 工作空间弹窗 -->
    <div v-if="showAddWsPopup" class="add-ws-popup">
      <div class="add-ws-tabs">
        <button :class="['add-ws-tab', { active: wsPopupTab === 'add' }]" @click="wsPopupTab = 'add'">添加</button>
        <button :class="['add-ws-tab', { active: wsPopupTab === 'restore' }]" @click="wsPopupTab = 'restore'">恢复</button>
      </div>

      <template v-if="wsPopupTab === 'add'">
        <div class="add-ws-field">
          <div class="add-ws-path-row">
            <input v-model="addWsPath" placeholder="输入文件夹路径，或点击 📂 浏览" class="add-ws-input add-ws-input-path" @keyup.enter="addWsByPath" />
            <button class="add-ws-browse" @click="openDirPicker()" title="浏览目录">📂</button>
          </div>
          <div class="add-ws-hint">名称自动取文件夹名</div>
        </div>
        <div v-if="addWsError" class="add-ws-error">{{ addWsError }}</div>
        <div class="add-ws-actions">
          <button class="add-ws-btn-add" @click="addWsByPath" :disabled="!addWsPath.trim() || addWsSubmitting">
            {{ addWsSubmitting ? '添加中…' : '添加' }}
          </button>
          <button class="add-ws-btn-ghost" @click="showAddWsPopup = false; wsPopupTab = 'add'">取消</button>
        </div>
      </template>

      <template v-if="wsPopupTab === 'restore'">
        <div v-if="removedWorkspaces.length === 0" class="add-ws-empty">无已移除的工作空间</div>
        <div v-for="r in removedWorkspaces" :key="r.name" class="removed-ws-item">
          <div class="removed-ws-info">
            <span class="removed-ws-name">{{ r.name }}</span>
            <span v-if="r.project_path" class="removed-ws-path">{{ r.project_path }}</span>
          </div>
          <div class="removed-ws-actions">
            <button class="removed-ws-btn-restore" @click="restoreWs(r.name)">恢复</button>
            <button class="removed-ws-btn-delete" @click="deleteRemovedWs(r.name)" title="彻底删除">🗑</button>
          </div>
        </div>
      </template>
    </div>

    <div class="sidebar-body">
      <!-- Workspace groups -->
      <div v-for="(ws, wsIdx) in workspaces" :key="ws.name" class="ws-group">
        <div class="ws-group-header" @click="toggleCollapse(ws.name)">
          <span class="ws-collapse-icon">{{ wsCollapsed[ws.name] ? '▸' : '▾' }}</span>
          <span class="ws-group-icon">📂</span>
          <span class="ws-group-name">{{ ws.name }}</span>
          <div class="ws-actions">
            <button class="ws-add-btn" @click.stop="newSessionFor(ws.name)" title="新建会话">+</button>
            <button class="ws-add-btn" @click.stop="enterBatchMode(ws.name)" :title="batchMode ? '取消批量' : '批量删除'" :style="{ color: batchMode && batchTargetWs === ws.name ? 'var(--accent)' : '' }">☷</button>
            <button v-if="ws.name !== 'default'" class="ws-action-btn" @click.stop="removeWs(ws.name)" title="移除工作空间">✕</button>
            <button v-if="ws.name !== 'default'" class="ws-action-btn ws-action-danger" @click.stop="deleteWs(ws.name)" title="删除工作空间及数据">🗑</button>
          </div>
          <div v-if="batchMode && batchTargetWs === ws.name" class="batch-inline-bar" @click.stop>
            <button class="batch-btn" @click.stop="selectAllForWs">全选</button>
            <button class="batch-btn danger" @click.stop="batchDelete" :disabled="selectedSessions.size === 0">删除 ({{ selectedSessions.size }})</button>
          </div>
        </div>
        <div v-if="!wsCollapsed[ws.name]" class="ws-group-sessions">
          <div v-for="s in (wsSessions[ws.name] || [])" :key="s.session_id"
               class="session-item" :class="{ active: !batchMode && activeSessionId === s.session_id, selected: batchMode && batchTargetWs === ws.name && selectedSessions.has(s.session_id) }"
               @click="batchMode && batchTargetWs === ws.name ? toggleSelect(s.session_id) : (!batchMode ? selectSession(s.session_id, ws.name) : null)"
               @contextmenu="onContextMenu($event, s.session_id, ws.name)"
               @dblclick="batchMode ? null : startEdit(s.session_id, s.name)">
            <div class="session-row">
              <span v-if="batchMode && batchTargetWs === ws.name" class="batch-check" :class="{ checked: selectedSessions.has(s.session_id) }">●</span>
              <span v-else-if="!batchMode && s.status === 'generating'" class="session-dot generating"></span>
              <span v-else-if="!batchMode" class="session-dot idle"></span>
              <template v-if="editingSid === s.session_id">
                <input class="session-edit-input" v-model="editingName"
                       @keydown="handleEditKey($event, s.session_id)"
                       @blur="finishEdit(s.session_id)" autofocus />
              </template>
              <template v-else>
                <span class="session-name">{{ s.name || s.preview || '新会话' }}</span>
              </template>
            </div>
            <div class="session-meta">{{ s.message_count }} 条{{ s.updated_at ? ' · ' + relativeTime(s.updated_at) : '' }}</div>
          </div>
          <div v-if="!(wsSessions[ws.name] || []).length" class="session-empty-sm">暂无会话</div>
        </div>
      </div>
    </div>

    <div class="sidebar-collapse" @click="emit('toggle')">
      ◀ 收起
    </div>

    <!-- Context menu -->
    <Teleport to="body">
      <div v-if="contextMenu" class="ctx-overlay" @click="closeContextMenu" @contextmenu.prevent="closeContextMenu">
        <div class="ctx-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }">
          <div class="ctx-item" @click="startEdit(contextMenu!.sid, getAllSessions().find(s => s.session_id === contextMenu!.sid)?.name || '')">重命名</div>
          <div class="ctx-item" @click="doExport(contextMenu!.sid)">导出 MD</div>
          <div class="ctx-item ctx-danger" @click="doDelete(contextMenu!.sid)">删除</div>
        </div>
      </div>
    </Teleport>

        <!-- Directory picker -->
    <Teleport to="body">
      <div v-if="showDirPicker" class="dir-overlay" @click="showDirPicker = false">
        <div class="dir-panel" @click.stop>
          <div class="dir-header">
            <h3 class="dir-title">选择目录</h3>
            <button class="dir-close" @click="showDirPicker = false">✕</button>
          </div>
          <div class="dir-breadcrumb">
            <button v-if="dirParent" class="dir-up" @click="loadDir(dirParent)">⬆ 上级</button>
            <span class="dir-current">{{ dirCurrent }}</span>
          </div>
          <div class="dir-list">
            <div v-if="dirLoading" class="dir-loading">加载中…</div>
            <div v-else-if="dirDirs.length === 0" class="dir-empty">无子目录</div>
            <div v-for="d in dirDirs" :key="d.path" class="dir-item" @click="loadDir(d.path)">
              <span class="dir-item-icon">{{ d.has_children ? '📁' : '📂' }}</span>
              <span class="dir-item-name">{{ d.name }}</span>
            </div>
          </div>
          <div class="dir-footer">
            <span class="dir-selected">{{ addWsPath || '未选择' }}</span>
            <div class="dir-footer-actions">
              <button class="ws-mgr-btn ws-mgr-btn-ghost" @click="showDirPicker = false">取消</button>
              <button class="ws-mgr-btn" @click="selectDir(dirCurrent)">选择此目录</button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>

  <div v-else class="sidebar-collapsed" @click="emit('toggle')">
    <div class="collapsed-icon">📂</div>
    <div class="collapsed-expand">▶</div>
  </div>
</template>

<style scoped>
.sidebar {
  flex-shrink: 0; display: flex; flex-direction: column;
  border-right: 0.5px solid var(--border); background: var(--bg); overflow: hidden;
}

.sidebar-collapsed {
  width: 40px; flex-shrink: 0; display: flex; flex-direction: column;
  align-items: center; padding-top: 1rem; gap: 0.6rem;
  border-right: 0.5px solid var(--border); background: var(--bg-card); cursor: pointer;
}
.collapsed-icon { font-size: 1.1rem; }
.collapsed-expand { font-size: 0.7rem; color: var(--fg-dim); margin-top: auto; margin-bottom: 1rem; }

/* Header */
.sidebar-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.7rem 0.9rem; border-bottom: 0.5px solid var(--border);
}
.sidebar-brand {
  font-family: 'Playfair Display', serif; font-size: 1rem; font-weight: 600; color: var(--fg);
}
.sidebar-brand::after { content: '.'; color: var(--accent); }
.ws-add-ws-btn {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
  color: var(--accent);
  font-size: 0.78rem;
  padding: 0.2rem 0.6rem;
  cursor: pointer;
  font-family: 'Source Sans 3', sans-serif;
  transition: all 0.2s ease;
}
.ws-add-ws-btn:hover { border-color: var(--accent); background: var(--bg-thinking); }

.add-ws-popup {
  margin: 0 0.8rem 0.5rem;
  padding: 0.8rem;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  animation: fadeIn 0.15s ease;
}
.add-ws-popup-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 0.6rem;
}
.add-ws-mode-tabs {
  display: flex;
  gap: 2px;
  margin-bottom: 0.7rem;
  background: var(--bg);
  border-radius: 6px;
  padding: 2px;
}
.add-ws-tab {
  flex: 1;
  padding: 0.3rem 0;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--fg-dim);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.15s ease;
}
.add-ws-tab.active {
  background: var(--accent);
  color: #fff;
  font-weight: 500;
}
.add-ws-tab:not(.active):hover {
  background: var(--bg-thinking);
}
.add-ws-field {
  margin-bottom: 0.5rem;
}
.add-ws-field label {
  display: block;
  font-size: 0.72rem;
  color: var(--fg-dim);
  margin-bottom: 0.15rem;
}
.add-ws-input {
  width: 100%;
  padding: 0.35rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--bg);
  color: var(--fg);
  font-size: 0.82rem;
  font-family: 'JetBrains Mono', monospace;
  outline: none;
  box-sizing: border-box;
}
.add-ws-input:focus { border-color: var(--accent); }
.add-ws-path-row { display: flex; gap: 0.3rem; }
.add-ws-input-path { flex: 1; }
.add-ws-browse {
  width: 30px; height: 30px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--bg);
  cursor: pointer;
  font-size: 0.85rem;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.add-ws-browse:hover { border-color: var(--accent); }
.add-ws-actions { display: flex; gap: 0.4rem; margin-top: 0.6rem; }
.add-ws-hint { font-size: 0.7rem; color: var(--fg-dim); margin-top: 0.2rem; }
.add-ws-error { font-size: 0.75rem; color: #e55; margin-top: 0.3rem; }
.add-ws-empty { font-size: 0.8rem; color: var(--fg-dim); text-align: center; padding: 1rem 0; }
.add-ws-tabs { display: flex; gap: 0; margin-bottom: 0.6rem; border-bottom: 0.5px solid var(--border-light); }
.add-ws-tab { border: none; background: none; color: var(--fg-dim); font-size: 0.8rem; padding: 0.3rem 0.8rem; cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.15s ease; }
.add-ws-tab.active { color: var(--fg); border-bottom-color: var(--accent); }
.add-ws-tab:hover:not(.active) { color: var(--fg); }
.add-ws-btn-add {
  padding: 0.3rem 0.8rem;
  border: none;
  border-radius: 5px;
  background: var(--accent);
  color: #fff;
  font-size: 0.82rem;
  cursor: pointer;
}
.add-ws-btn-add:disabled { opacity: 0.4; cursor: default; }
.add-ws-btn-ghost {
  padding: 0.3rem 0.6rem;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--fg-dim);
  font-size: 0.82rem;
  cursor: pointer;
}
.add-ws-btn-ghost:hover { color: var(--fg); }

.ws-mgr-btn {
  padding: 0.25rem 0.5rem; border: 0.5px solid var(--border); border-radius: 6px;
  background: var(--bg-card); color: var(--fg-muted); font-size: 0.75rem;
  cursor: pointer; display: flex; align-items: center; gap: 0.3rem;
  transition: all 0.2s ease;
}
.ws-mgr-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--bg-thinking); }

/* Body (scrollable) */
.sidebar-body { flex: 1; overflow-y: auto; }

/* Workspace group */
.ws-group { border-bottom: 0.5px solid var(--border-light); }
.ws-group-header {
  display: flex; align-items: center; gap: 0.35rem;
  padding: 0.55rem 0.7rem; cursor: pointer; transition: background 0.15s ease;
  user-select: none;
}
.ws-group-header:hover { background: var(--bg-card); }
.ws-collapse-icon { font-size: 0.65rem; color: var(--fg-dim); width: 10px; }
.ws-group-icon { font-size: 0.85rem; }
.ws-group-name {
  font-family: 'Playfair Display', serif; font-size: 0.9rem; font-weight: 600;
  color: var(--fg); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ws-actions {
  display: flex; gap: 0.2rem; opacity: 0; transition: opacity 0.15s ease;
}
.ws-group-header:hover .ws-actions { opacity: 1; }

.ws-add-btn {
  width: 18px; height: 18px; border: none; background: none;
  color: var(--accent); font-size: 0.85rem; font-weight: 700; cursor: pointer;
  border-radius: 4px; display: flex; align-items: center; justify-content: center;
  transition: background 0.15s ease;
}
.ws-add-btn:hover { background: var(--bg-thinking); }

.ws-action-btn {
  width: 18px; height: 18px; border: none; background: none;
  color: var(--fg-dim); font-size: 0.7rem; cursor: pointer; border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s ease;
}
.ws-action-btn:hover { background: var(--bg-card); color: var(--fg); }
.ws-action-danger:hover { color: #e55; }

/* Sessions under group */
.ws-group-sessions { padding: 0; }

.session-item {
  padding: 0.45rem 0.7rem 0.4rem 1.8rem; border-bottom: 0.5px solid var(--border-light);
  cursor: pointer; transition: background 0.15s ease; border-left: 2px solid transparent;
}
.session-item:hover { background: var(--bg-card); }
.session-item.selected { background: rgba(232, 145, 45, 0.12); border-left-color: var(--accent); }
.batch-check { font-size: 0.75rem; color: var(--border); flex-shrink: 0; }
.batch-check.checked { color: var(--accent); }
.batch-inline-bar { display: flex; gap: 0.4rem; padding: 0.2rem 0.6rem; border-bottom: 0.5px solid var(--border-light); background: rgba(232, 145, 45, 0.05); }
.batch-btn { font-size: 0.7rem; padding: 0.2rem 0.5rem; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-card); cursor: pointer; color: var(--fg-muted); }
.batch-btn.danger { color: #e55; border-color: #e55; }
.batch-btn:disabled { opacity: 0.4; cursor: default; }
.session-item.active { border-left-color: var(--accent); background: var(--bg-thinking); }

.session-row { display: flex; align-items: center; gap: 0.35rem; }

.session-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.session-dot.generating { background: #4CAF50; animation: pulse 1.5s ease-in-out infinite; }
.session-dot.idle { background: transparent; }

.session-name {
  font-size: 0.85rem; font-weight: 500; color: var(--fg);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
}

.session-edit-input {
  flex: 1; font-size: 0.85rem; font-family: 'Source Sans 3', sans-serif;
  color: var(--fg); background: var(--bg-input); border: none;
  border-bottom: 1px solid var(--accent); outline: none; padding: 0 0.1rem;
}

.session-meta {
  font-family: 'JetBrains Mono', monospace; font-size: 0.68rem; color: var(--fg-dim);
  padding-left: 1.05rem; margin-top: 0.1rem;
}

.session-empty-sm { padding: 0.6rem 1.8rem 0.5rem; color: var(--fg-dim); font-size: 0.78rem; }

.sidebar-collapse {
  padding: 0.5rem 1rem; text-align: right; font-size: 0.78rem; color: var(--fg-dim);
  cursor: pointer; border-top: 0.5px solid var(--border-light); transition: color 0.2s ease;
}
.sidebar-collapse:hover { color: var(--fg); }

/* Context menu */
.ctx-overlay { position: fixed; inset: 0; z-index: 300; }
.ctx-menu {
  position: fixed; background: var(--bg-card); border: 0.5px solid var(--border);
  border-radius: 6px; box-shadow: 0 4px 12px var(--shadow); padding: 0.3rem 0;
  z-index: 301; min-width: 100px;
}
.ctx-item { padding: 0.45rem 0.8rem; font-size: 0.85rem; color: var(--fg); cursor: pointer; transition: background 0.1s ease; }
.ctx-item:hover { background: var(--bg-thinking); }
.ctx-danger:hover { color: #e55; }

/* Workspace manager */
.ws-mgr-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 200; animation: fadeIn 0.2s ease; }
.ws-mgr-panel {
  position: fixed; top: 0; right: 0; bottom: 0; width: 340px;
  background: var(--bg); border-left: 0.5px solid var(--border);
  box-shadow: -4px 0 20px var(--shadow); display: flex; flex-direction: column;
  animation: slideIn 0.25s ease;
}
.ws-mgr-header { display: flex; align-items: center; justify-content: space-between; padding: 1rem 1.2rem; border-bottom: 0.5px solid var(--border); }
.ws-mgr-title { font-family: 'Playfair Display', serif; font-size: 1.1rem; font-weight: 600; color: var(--fg); }
.ws-mgr-close { width: 28px; height: 28px; border: none; background: none; color: var(--fg-dim); font-size: 1rem; cursor: pointer; border-radius: 4px; display: flex; align-items: center; justify-content: center; transition: all 0.2s ease; }
.ws-mgr-close:hover { background: var(--bg-card); color: var(--fg); }
.ws-mgr-list { flex: 1; overflow-y: auto; padding: 0.5rem 0; }
.ws-mgr-item { display: flex; align-items: center; justify-content: space-between; padding: 0.7rem 1.2rem; border-bottom: 0.5px solid var(--border-light); transition: background 0.15s ease; }
.ws-mgr-item:hover { background: var(--bg-thinking); }
.ws-mgr-info { flex: 1; min-width: 0; }
.ws-mgr-item-name { font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; font-weight: 500; color: var(--fg); display: block; }
.ws-mgr-item-path { font-size: 0.75rem; color: var(--fg-dim); display: block; margin-top: 0.15rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ws-mgr-actions { display: flex; gap: 0.3rem; flex-shrink: 0; }
.ws-mgr-btn-sm { width: 24px; height: 24px; border: none; background: none; color: var(--fg-dim); font-size: 0.75rem; cursor: pointer; border-radius: 4px; display: flex; align-items: center; justify-content: center; transition: all 0.2s ease; }
.ws-mgr-btn-sm:hover { background: var(--bg-card); color: var(--fg); }
.ws-mgr-btn-danger:hover { color: #e53e3e; }
.ws-mgr-footer { padding: 1rem 1.2rem; border-top: 0.5px solid var(--border); }
.ws-mgr-footer-actions { display: flex; gap: 0.5rem; }
.ws-mgr-form { display: flex; flex-direction: column; gap: 0.5rem; }
.ws-mgr-path-row { display: flex; gap: 0.4rem; align-items: center; }
.ws-mgr-input-path { flex: 1; }
.ws-mgr-browse-btn { width: 32px; height: 32px; border: 0.5px solid var(--border); border-radius: 6px; background: var(--bg-card); cursor: pointer; font-size: 0.9rem; display: flex; align-items: center; justify-content: center; transition: all 0.2s ease; flex-shrink: 0; }
.ws-mgr-browse-btn:hover { border-color: var(--accent); background: var(--bg-thinking); }
.ws-mgr-input { width: 100%; padding: 0.5rem 0.7rem; border: 0.5px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--fg); font-size: 0.85rem; font-family: 'JetBrains Mono', monospace; outline: none; transition: border-color 0.2s ease; }
.ws-mgr-input:focus { border-color: var(--accent); }
.ws-mgr-form-actions { display: flex; gap: 0.5rem; }
.ws-add-ws-btn {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
  color: var(--accent);
  font-size: 0.78rem;
  padding: 0.2rem 0.6rem;
  cursor: pointer;
  font-family: 'Source Sans 3', sans-serif;
  transition: all 0.2s ease;
}
.ws-add-ws-btn:hover { border-color: var(--accent); background: var(--bg-thinking); }

.add-ws-popup {
  margin: 0 0.8rem 0.5rem;
  padding: 0.8rem;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  animation: fadeIn 0.15s ease;
}
.add-ws-popup-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 0.6rem;
}
.add-ws-mode-tabs {
  display: flex;
  gap: 2px;
  margin-bottom: 0.7rem;
  background: var(--bg);
  border-radius: 6px;
  padding: 2px;
}
.add-ws-tab {
  flex: 1;
  padding: 0.3rem 0;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--fg-dim);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.15s ease;
}
.add-ws-tab.active {
  background: var(--accent);
  color: #fff;
  font-weight: 500;
}
.add-ws-tab:not(.active):hover {
  background: var(--bg-thinking);
}
.add-ws-field {
  margin-bottom: 0.5rem;
}
.add-ws-field label {
  display: block;
  font-size: 0.72rem;
  color: var(--fg-dim);
  margin-bottom: 0.15rem;
}
.add-ws-input {
  width: 100%;
  padding: 0.35rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--bg);
  color: var(--fg);
  font-size: 0.82rem;
  font-family: 'JetBrains Mono', monospace;
  outline: none;
  box-sizing: border-box;
}
.add-ws-input:focus { border-color: var(--accent); }
.add-ws-path-row { display: flex; gap: 0.3rem; }
.add-ws-input-path { flex: 1; }
.add-ws-browse {
  width: 30px; height: 30px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--bg);
  cursor: pointer;
  font-size: 0.85rem;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.add-ws-browse:hover { border-color: var(--accent); }
.add-ws-actions { display: flex; gap: 0.4rem; margin-top: 0.6rem; }
.add-ws-btn-add {
  padding: 0.3rem 0.8rem;
  border: none;
  border-radius: 5px;
  background: var(--accent);
  color: #fff;
  font-size: 0.82rem;
  cursor: pointer;
}
.add-ws-btn-add:disabled { opacity: 0.4; cursor: default; }
.add-ws-btn-ghost {
  padding: 0.3rem 0.6rem;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--fg-dim);
  font-size: 0.82rem;
  cursor: pointer;
}
.add-ws-btn-ghost:hover { color: var(--fg); }

.ws-mgr-btn { padding: 0.4rem 0.8rem; border: 0.5px solid var(--border); border-radius: 6px; background: var(--bg-card); color: var(--fg); font-size: 0.8rem; cursor: pointer; transition: all 0.2s ease; }
.ws-mgr-btn:hover { background: var(--bg-thinking); }
.ws-mgr-btn-ghost { background: transparent; border-color: transparent; color: var(--fg-dim); }
.ws-mgr-btn-ghost:hover { color: var(--fg); background: var(--bg-thinking); }

/* Directory picker */
.dir-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 250; animation: fadeIn 0.2s ease; }
.dir-panel { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 480px; max-height: 70vh; background: var(--bg); border: 0.5px solid var(--border); border-radius: 12px; box-shadow: 0 8px 32px var(--shadow); display: flex; flex-direction: column; animation: fadeIn 0.2s ease; }
.dir-header { display: flex; align-items: center; justify-content: space-between; padding: 0.8rem 1.2rem; border-bottom: 0.5px solid var(--border); }
.dir-title { font-family: 'Playfair Display', serif; font-size: 1rem; font-weight: 600; color: var(--fg); }
.dir-close { width: 28px; height: 28px; border: none; background: none; color: var(--fg-dim); font-size: 1rem; cursor: pointer; border-radius: 4px; display: flex; align-items: center; justify-content: center; transition: all 0.2s ease; }
.dir-close:hover { background: var(--bg-card); color: var(--fg); }
.dir-breadcrumb { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 1.2rem; border-bottom: 0.5px solid var(--border-light); }
.dir-up { border: none; background: none; color: var(--accent); cursor: pointer; font-size: 0.85rem; padding: 0.2rem 0.4rem; border-radius: 4px; transition: background 0.15s ease; }
.dir-up:hover { background: var(--bg-thinking); }
.dir-current { font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; color: var(--fg-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dir-list { flex: 1; overflow-y: auto; padding: 0.3rem 0; min-height: 200px; }
.dir-loading, .dir-empty { padding: 2rem; text-align: center; color: var(--fg-dim); font-size: 0.85rem; }
.dir-item { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 1.2rem; cursor: pointer; transition: background 0.15s ease; }
.dir-item:hover { background: var(--bg-thinking); }
.dir-item-icon { font-size: 0.9rem; }
.dir-item-name { font-size: 0.88rem; color: var(--fg); }
.dir-footer { display: flex; align-items: center; justify-content: space-between; padding: 0.7rem 1.2rem; border-top: 0.5px solid var(--border); }
.dir-selected { font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; color: var(--fg-dim); max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dir-footer-actions { display: flex; gap: 0.5rem; }

@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.removed-ws-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.4rem 0;
  border-bottom: 0.5px solid var(--border-light);
}
.removed-ws-item:last-child { border-bottom: none; }
.removed-ws-info { flex: 1; min-width: 0; }
.removed-ws-name { font-size: 0.85rem; font-weight: 500; color: var(--fg); }
.removed-ws-path { font-size: 0.72rem; color: var(--fg-dim); display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.removed-ws-actions { display: flex; gap: 0.3rem; }
.removed-ws-btn-restore {
  padding: 0.2rem 0.6rem;
  border: none;
  border-radius: 5px;
  background: var(--accent);
  color: #fff;
  font-size: 0.78rem;
  cursor: pointer;
}
.removed-ws-btn-restore:hover { background: var(--accent-hover); }
.removed-ws-btn-delete {
  padding: 0.2rem 0.4rem;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--fg-dim);
  font-size: 0.8rem;
  cursor: pointer;
  opacity: 0.5;
}
.removed-ws-btn-delete:hover { color: #e55; opacity: 1; }
.removed-ws-empty { font-size: 0.82rem; color: var(--fg-dim); text-align: center; padding: 0.8rem; }
</style>
