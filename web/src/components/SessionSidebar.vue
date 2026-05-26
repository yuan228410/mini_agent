<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue'
import {
  getSessions, createSession, deleteSession, renameSession,
  getWorkspaces, createWorkspace, addWorkspace, removeWorkspace, switchWorkspace,
  browseDirs,
  type SessionInfo, type WorkspaceInfo, type BrowseDir,
} from '../api'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits(['switch-session', 'status-change', 'toggle', 'workspace-change'])

const WORKSPACE_KEY = 'mini-ai-active-workspace'

const workspaces = ref<WorkspaceInfo[]>([])
const activeWorkspace = ref<string | null>(null)
const activeSessionId = ref('')
const editingSid = ref('')
const editingName = ref('')
const contextMenu = ref<{ x: number; y: number; sid: string } | null>(null)

const wsSessions: Record<string, SessionInfo[]> = reactive({})
const wsCollapsed: Record<string, boolean> = reactive({})

const showWsManager = ref(false)
const showCreateWs = ref(false)
const newWsName = ref('')
const newWsPath = ref('')
const showAddWs = ref(false)
const addWsPath = ref('')

const showDirPicker = ref(false)
const dirTarget = ref<'create' | 'add'>('create')
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
}

async function loadWorkspaces() {
  try {
    const resp = await getWorkspaces()
    workspaces.value = resp.workspaces || []
  } catch {}
}

async function loadAllSessions() {
  const promises: Promise<void>[] = []
  promises.push((async () => {

  })())
  for (const ws of workspaces.value) {
    promises.push((async () => {
      try { const resp = await getSessions(ws.name); wsSessions[ws.name] = resp.sessions || [] } catch { wsSessions[ws.name] = [] }
    })())
  }
  await Promise.all(promises)
}

async function loadSessionsFor(wsName: string | null) {
  try {
    const resp = await getSessions(wsName || undefined)
    if (wsName) {
      wsSessions[wsName] = resp.sessions || []
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
      activeSessionId.value = resp.session_id
      activeWorkspace.value = wsName
      emit('switch-session', resp.session_id, wsName)
    }
  } catch {}
}

function onContextMenu(e: MouseEvent, sid: string) {
  e.preventDefault()
  contextMenu.value = { x: e.clientX, y: e.clientY, sid }
}

function closeContextMenu() {
  contextMenu.value = null
}

async function doDelete(sid: string) {
  contextMenu.value = null
  await deleteSession(sid)
  await loadAllSessions()
  if (activeSessionId.value === sid) {
    const all = getAllSessions()
    if (all.length > 0) {
      activeSessionId.value = all[0].session_id
      emit('switch-session', all[0].session_id, null)
    }
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
  await renameSession(sid, name)
  await loadAllSessions()
}

function handleEditKey(e: KeyboardEvent, sid: string) {
  if (e.key === 'Enter') finishEdit(sid)
  if (e.key === 'Escape') { editingSid.value = '' }
}

async function openDirPicker(target: 'create' | 'add') {
  dirTarget.value = target
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
  if (dirTarget.value === 'create') newWsPath.value = path
  else addWsPath.value = path
  showDirPicker.value = false
}

async function createWs() {
  const name = newWsName.value.trim()
  if (!name) return
  await createWorkspace(name, newWsPath.value.trim())
  newWsName.value = ''
  newWsPath.value = ''
  showCreateWs.value = false
  await loadAll()
}

async function addWs() {
  const path = addWsPath.value.trim()
  if (!path) return
  await addWorkspace(path)
  addWsPath.value = ''
  showAddWs.value = false
  await loadAll()
}

function confirmDeleteWs(name: string) {
  if (confirm(`确定删除工作空间 "${name}" 及其所有数据？此操作不可恢复！`)) {
    deleteWs(name)
  }
}

async function removeWs(name: string) {
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

function updateSessionStatus(sid: string, status: 'idle' | 'generating') {
  for (const ws of workspaces.value) {
    const s = (wsSessions[ws.name] || []).find(s => s.session_id === sid)
    if (s) { s.status = status; return }
  }
  return null
}

function setActiveSession(sid: string) {
  activeSessionId.value = sid
}

defineExpose({ loadSessions: loadAllSessions, updateSessionStatus, setActiveSession, activeWorkspace })
</script>

<template>
  <div v-if="visible" class="sidebar" @click="closeContextMenu">
    <div class="sidebar-header">
      <span class="sidebar-brand">mini_ai</span>
      <button class="ws-mgr-btn" @click="showWsManager = !showWsManager" title="管理工作空间">⚙ 管理</button>
    </div>

    <div class="sidebar-body">
      <!-- Workspace groups -->
      <div v-for="ws in workspaces" :key="ws.name" class="ws-group">
        <div class="ws-group-header" @click="toggleCollapse(ws.name)">
          <span class="ws-collapse-icon">{{ wsCollapsed[ws.name] ? '▸' : '▾' }}</span>
          <span class="ws-group-icon">📂</span>
          <span class="ws-group-name">{{ ws.name }}</span>
          <div class="ws-actions">
            <button class="ws-add-btn" @click.stop="newSessionFor(ws.name)" title="新建会话">+</button>
            <button v-if="ws.name !== 'default'" class="ws-action-btn" @click.stop="removeWs(ws.name)" title="移除工作空间">✕</button>
            <button v-if="ws.name !== 'default'" class="ws-action-btn ws-action-danger" @click.stop="confirmDeleteWs(ws.name)" title="删除工作空间及数据">🗑</button>
          </div>
        </div>
        <div v-if="!wsCollapsed[ws.name]" class="ws-group-sessions">
          <div v-for="s in (wsSessions[ws.name] || [])" :key="s.session_id"
               class="session-item" :class="{ active: activeSessionId === s.session_id }"
               @click="selectSession(s.session_id, ws.name)"
               @contextmenu="onContextMenu($event, s.session_id)"
               @dblclick="startEdit(s.session_id, s.name)">
            <div class="session-row">
              <span v-if="s.status === 'generating'" class="session-dot generating"></span>
              <span v-else class="session-dot idle"></span>
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
          <div class="ctx-item ctx-danger" @click="doDelete(contextMenu!.sid)">删除</div>
        </div>
      </div>
    </Teleport>

    <!-- Workspace manager -->
    <Teleport to="body">
      <div v-if="showWsManager" class="ws-mgr-overlay" @click="showWsManager = false">
        <div class="ws-mgr-panel" @click.stop>
          <div class="ws-mgr-header">
            <h3 class="ws-mgr-title">工作空间</h3>
            <button class="ws-mgr-close" @click="showWsManager = false">✕</button>
          </div>
          <div class="ws-mgr-list">
            <div v-for="ws in workspaces" :key="ws.name" class="ws-mgr-item">
              <div class="ws-mgr-info">
                <span class="ws-mgr-item-name">{{ ws.name }}</span>
                <span v-if="ws.project_path" class="ws-mgr-item-path">{{ ws.project_path }}</span>
              </div>
              <div v-if="ws.name !== 'default'" class="ws-mgr-actions">
                <button class="ws-mgr-btn-sm" title="移除" @click="removeWs(ws.name)">✕</button>
                <button class="ws-mgr-btn-sm ws-mgr-btn-danger" title="删除数据" @click="deleteWs(ws.name)">🗑</button>
              </div>
            </div>
          </div>
          <div class="ws-mgr-footer">
            <div v-if="showCreateWs" class="ws-mgr-form">
              <input v-model="newWsName" placeholder="名称" class="ws-mgr-input" @keyup.enter="createWs" />
              <div class="ws-mgr-path-row">
                <input v-model="newWsPath" placeholder="项目路径（可选）" class="ws-mgr-input ws-mgr-input-path" />
                <button class="ws-mgr-browse-btn" @click="openDirPicker('create')" title="浏览目录">📂</button>
              </div>
              <div class="ws-mgr-form-actions">
                <button class="ws-mgr-btn" @click="createWs">创建</button>
                <button class="ws-mgr-btn ws-mgr-btn-ghost" @click="showCreateWs = false">取消</button>
              </div>
            </div>
            <div v-else-if="showAddWs" class="ws-mgr-form">
              <div class="ws-mgr-path-row">
                <input v-model="addWsPath" placeholder="现有文件夹路径" class="ws-mgr-input ws-mgr-input-path" @keyup.enter="addWs" />
                <button class="ws-mgr-browse-btn" @click="openDirPicker('add')" title="浏览目录">📂</button>
              </div>
              <div class="ws-mgr-form-actions">
                <button class="ws-mgr-btn" @click="addWs">添加</button>
                <button class="ws-mgr-btn ws-mgr-btn-ghost" @click="showAddWs = false">取消</button>
              </div>
            </div>
            <div v-else class="ws-mgr-footer-actions">
              <button class="ws-mgr-btn" @click="showCreateWs = true">+ 新建</button>
              <button class="ws-mgr-btn ws-mgr-btn-ghost" @click="showAddWs = true">+ 添加现有</button>
            </div>
          </div>
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
            <span class="dir-selected">{{ dirTarget === 'create' ? newWsPath : addWsPath || '未选择' }}</span>
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
  width: 260px; flex-shrink: 0; display: flex; flex-direction: column;
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
</style>
