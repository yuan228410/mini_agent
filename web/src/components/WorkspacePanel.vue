<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  addWorkspace,
  createWorkspace,
  getWorkspaces,
  removeWorkspace,
  switchWorkspace,
} from '../api'
import type { WorkspaceInfo } from '../api'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits(['close', 'switched'])

const workspaces = ref<WorkspaceInfo[]>([])
const active = ref('')
const showCreate = ref(false)
const newName = ref('')
const newPath = ref('')
const addPath = ref('')
const showAdd = ref(false)

onMounted(async () => { await refresh() })

async function refresh() {
  try {
    const data = await getWorkspaces()
    workspaces.value = data.workspaces || []
    active.value = data.active || 'default'
  } catch {}
}

async function switchTo(name: string) {
  const data = await switchWorkspace(name)
  active.value = name
  emit('switched', name, data.session_id || 'default')
}

async function create() {
  const name = newName.value.trim()
  if (!name) return
  await createWorkspace(name, newPath.value.trim())
  newName.value = ''
  newPath.value = ''
  showCreate.value = false
  await refresh()
}

async function addExisting() {
  const path = addPath.value.trim()
  if (!path) return
  await addWorkspace(path)
  addPath.value = ''
  showAdd.value = false
  await refresh()
}

async function remove(name: string) {
  if (name === 'default') return
  await removeWorkspace(name)
  await refresh()
}

async function deleteWs(name: string) {
  if (name === 'default') return
  if (!confirm(`确定删除工作空间 "${name}" 及其所有数据？`)) return
  await removeWorkspace(name, true)
  await refresh()
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="ws-overlay" @click="emit('close')">
      <div class="ws-panel" @click.stop>
        <div class="ws-header">
          <h3 class="ws-title">工作空间</h3>
          <button class="ws-close" @click="emit('close')">✕</button>
        </div>

        <div class="ws-list">
          <div
            v-for="ws in workspaces"
            :key="ws.name"
            class="ws-item"
            :class="{ active: ws.name === active }"
          >
            <div class="ws-item-info" @click="switchTo(ws.name)">
              <span class="ws-item-name">
                <span v-if="ws.name === active" class="ws-dot">●</span>
                {{ ws.name }}
              </span>
              <span v-if="ws.project_path" class="ws-item-path">{{ ws.project_path }}</span>
            </div>
            <div v-if="ws.name !== 'default'" class="ws-item-actions">
              <button class="ws-btn-sm" title="移除" @click="remove(ws.name)">✕</button>
              <button class="ws-btn-sm ws-btn-danger" title="删除数据" @click="deleteWs(ws.name)">🗑</button>
            </div>
          </div>
        </div>

        <div class="ws-footer">
          <div v-if="showCreate" class="ws-form">
            <input v-model="newName" placeholder="名称" class="ws-input" @keyup.enter="create" />
            <input v-model="newPath" placeholder="项目路径（可选）" class="ws-input" />
            <div class="ws-form-actions">
              <button class="ws-btn" @click="create">创建</button>
              <button class="ws-btn ws-btn-ghost" @click="showCreate = false">取消</button>
            </div>
          </div>
          <div v-else-if="showAdd" class="ws-form">
            <input v-model="addPath" placeholder="现有文件夹路径" class="ws-input" @keyup.enter="addExisting" />
            <div class="ws-form-actions">
              <button class="ws-btn" @click="addExisting">添加</button>
              <button class="ws-btn ws-btn-ghost" @click="showAdd = false">取消</button>
            </div>
          </div>
          <div v-else class="ws-footer-actions">
            <button class="ws-btn" @click="showCreate = true">+ 新建</button>
            <button class="ws-btn ws-btn-ghost" @click="showAdd = true">+ 添加现有</button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.ws-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  z-index: 200;
  animation: fadeIn 0.2s ease;
}

.ws-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 360px;
  background: var(--bg);
  border-left: 0.5px solid var(--border);
  box-shadow: -4px 0 20px var(--shadow);
  display: flex;
  flex-direction: column;
  animation: slideIn 0.25s ease;
}

@keyframes slideIn {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.ws-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.2rem;
  border-bottom: 0.5px solid var(--border);
}

.ws-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--fg);
}

.ws-close {
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
  transition: all 0.2s ease;
}

.ws-close:hover {
  background: var(--bg-card);
  color: var(--fg);
}

.ws-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0;
}

.ws-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.7rem 1.2rem;
  border-bottom: 0.5px solid var(--border-light);
  transition: background 0.15s ease;
}

.ws-item:hover {
  background: var(--bg-thinking);
}

.ws-item.active {
  background: var(--bg-card);
}

.ws-item-info {
  flex: 1;
  cursor: pointer;
  min-width: 0;
}

.ws-item-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--fg);
  display: block;
}

.ws-dot {
  color: var(--accent, #4a9eff);
  margin-right: 0.3rem;
}

.ws-item-path {
  font-size: 0.75rem;
  color: var(--fg-dim);
  display: block;
  margin-top: 0.15rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ws-item-actions {
  display: flex;
  gap: 0.3rem;
  flex-shrink: 0;
}

.ws-btn-sm {
  width: 24px;
  height: 24px;
  border: none;
  background: none;
  color: var(--fg-dim);
  font-size: 0.75rem;
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.ws-btn-sm:hover {
  background: var(--bg-card);
  color: var(--fg);
}

.ws-btn-danger:hover {
  color: #e53e3e;
}

.ws-footer {
  padding: 1rem 1.2rem;
  border-top: 0.5px solid var(--border);
}

.ws-footer-actions {
  display: flex;
  gap: 0.5rem;
}

.ws-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.ws-input {
  width: 100%;
  padding: 0.5rem 0.7rem;
  border: 0.5px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--fg);
  font-size: 0.85rem;
  font-family: 'JetBrains Mono', monospace;
  outline: none;
  transition: border-color 0.2s ease;
}

.ws-input:focus {
  border-color: var(--accent, #4a9eff);
}

.ws-form-actions {
  display: flex;
  gap: 0.5rem;
}

.ws-btn {
  padding: 0.4rem 0.8rem;
  border: 0.5px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
  color: var(--fg);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.ws-btn:hover {
  background: var(--bg-thinking);
}

.ws-btn-ghost {
  background: transparent;
  border-color: transparent;
  color: var(--fg-dim);
}

.ws-btn-ghost:hover {
  color: var(--fg);
  background: var(--bg-thinking);
}
</style>
