<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { getTeamStatus, getBlackboard, dismissTeammate, clearBlackboard } from '../api'

interface Teammate { name: string; role: string; status: string }
interface BbEntry { value: string; author: string; ts: number }

const props = defineProps<{ visible?: boolean, username?: string, workspace?: string, embedded?: boolean }>()
const emit = defineEmits(['close'])

const teammates = ref<Teammate[]>([])
const bbEntries = ref<Record<string, BbEntry>>({})
const hasTeam = ref(false)
const activeTab = ref<'teammates' | 'blackboard'>('teammates')
const expandedKeys = ref<Set<string>>(new Set())

onMounted(() => { refresh() })
onUnmounted(() => { if (_wsHandler) window.removeEventListener('ws-message', _wsHandler) })

watch(() => [props.visible, props.username, props.workspace], async ([vis]) => {
  if (vis) await refresh()
})

async function refresh() {
  if (!props.username) return
  try {
    const resp = await getTeamStatus(props.username, props.workspace || '')
    teammates.value = resp.teammates || []
    hasTeam.value = resp.has_team
  } catch { teammates.value = []; hasTeam.value = false }

  try {
    const resp = await getBlackboard(props.username, props.workspace || '')
    bbEntries.value = resp.entries || {}
  } catch { bbEntries.value = {} }
}

let _wsHandler: ((e: Event) => void) | null = null
function setupWsListener() {
  if (_wsHandler) window.removeEventListener('ws-message', _wsHandler)
  _wsHandler = ((e: CustomEvent) => {
    const data = e.detail
    if (!data) return
    if (data.event === 'teammate_status' && data.data) {
      const { name, status } = data.data
      const idx = teammates.value.findIndex(t => t.name === name)
      if (idx >= 0) teammates.value[idx].status = status
      else teammates.value.push({ name, role: '', status })
    }
    if (data.event === 'blackboard_update' && data.data) {
      refresh()
    }
  }) as EventListener
  window.addEventListener('ws-message', _wsHandler)
}
setupWsListener()

async function onClearBlackboard() {
  if (!props.username) return
  try {
    await clearBlackboard(props.username, props.workspace || '')
    bbEntries.value = {}
  } catch {}
}

async function onDismiss(name: string) {
  if (!confirm(`确定解散队友 "${name}"？`)) return
  try {
    await dismissTeammate(props.username!, props.workspace || '', name)
    await refresh()
  } catch { alert('解散失败') }
}

function toggleExpand(key: string) {
  if (expandedKeys.value.has(key)) expandedKeys.value.delete(key)
  else expandedKeys.value.add(key)
}

function statusColor(s: string) {
  if (s === 'working') return '#4caf50'
  if (s === 'idle') return '#ff9800'
  if (s === 'offline') return '#9e9e9e'
  if (s === 'shutdown') return '#f44336'
  return '#9e9e9e'
}

function statusLabel(s: string) {
  if (s === 'working') return '工作中'
  if (s === 'idle') return '空闲'
  if (s === 'offline') return '离线'
  if (s === 'shutdown') return '已退出'
  return s
}
</script>

<template>
<Teleport to="body" :disabled="!!embedded">
    <div v-if="visible || embedded" :class="[embedded ? 'team-overlay-embedded' : 'team-overlay']" @click="embedded ? null : emit('close')">
      <div :class="[embedded ? 'team-panel-embedded' : 'team-panel']" @click.stop>
        <div class="team-header">
          <h3 class="team-title">协作</h3>
          <button class="team-close" @click="emit('close')">✕</button>
        </div>
        <div class="panel-tabs">
          <button :class="['panel-tab', { active: activeTab === 'teammates' }]" @click="activeTab = 'teammates'">队友</button>
          <button :class="['panel-tab', { active: activeTab === 'blackboard' }]" @click="activeTab = 'blackboard'">黑板</button>
        </div>
        <div v-if="activeTab === 'teammates'" class="team-list">
          <div v-if="teammates.length === 0" class="team-empty">
            暂无队友<br>
            <span class="team-hint">在对话中让 AI 调用 spawn_teammate 召入队友</span>
          </div>
          <div v-for="m in teammates" :key="m.name" class="teammate-item">
            <div class="teammate-info">
              <span class="teammate-name">{{ m.name }}</span>
              <span v-if="m.role" class="teammate-role">{{ m.role }}</span>
              <span class="teammate-status" :style="{ color: statusColor(m.status) }">● {{ statusLabel(m.status) }}</span>
            </div>
            <button v-if="m.status === 'idle' || m.status === 'working'" class="teammate-dismiss" @click="onDismiss(m.name)" title="解散">🗑</button>
          </div>
        </div>
        <div v-if="activeTab === 'blackboard'" class="team-list">
          <div v-if="Object.keys(bbEntries).length > 0" class="bb-clear-bar">
            <button class="bb-clear-btn" @click="onClearBlackboard">🗑 清空黑板</button>
          </div>
          <div v-if="Object.keys(bbEntries).length === 0" class="team-empty">
            黑板为空<br>
            <span class="team-hint">Agent 可通过 blackboard_write 写入共享数据</span>
          </div>
          <div v-for="(entry, key) in bbEntries" :key="key" class="bb-item">
            <div class="bb-key" @click="toggleExpand(key as string)">
              <span>{{ key }}</span>
              <span v-if="entry.author" class="bb-author">by {{ entry.author }}</span>
              <span class="bb-toggle">{{ expandedKeys.has(key as string) ? '▾' : '▸' }}</span>
            </div>
            <div v-if="expandedKeys.has(key as string)" class="bb-value-full">{{ entry.value }}</div>
            <div v-else class="bb-value-preview">{{ (entry.value || '').slice(0, 100) }}{{ (entry.value || '').length > 100 ? '...' : '' }}</div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>

</template>

<style scoped>
.team-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.3);
  z-index: 200;
  animation: fadeIn 0.2s ease;
}
.team-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 340px;
  background: var(--bg);
  border-left: 0.5px solid var(--border);
  box-shadow: -4px 0 20px var(--shadow);
  display: flex;
  flex-direction: column;
  animation: slideIn 0.25s ease;
}
@keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.team-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.2rem;
  border-bottom: 0.5px solid var(--border);
}
.team-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--fg);
}
.team-close {
  width: 28px; height: 28px;
  border: none; background: none;
  color: var(--fg-dim); font-size: 1rem;
  cursor: pointer; border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.2s ease;
}
.team-close:hover { background: var(--bg-card); color: var(--fg); }
.panel-tabs {
  display: flex;
  gap: 2px;
  padding: 0.5rem 1.2rem;
  border-bottom: 0.5px solid var(--border);
  background: var(--bg);
}
.panel-tab {
  flex: 1;
  padding: 0.35rem 0;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--fg-dim);
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.15s ease;
}
.panel-tab.active {
  background: var(--accent);
  color: #fff;
  font-weight: 500;
}
.panel-tab:not(.active):hover { background: var(--bg-thinking); }
.team-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0;
}
.team-empty {
  padding: 2rem;
  text-align: center;
  color: var(--fg-dim);
  font-size: 0.9rem;
  line-height: 1.6;
}
.team-hint {
  font-size: 0.78rem;
  color: var(--fg-dim);
  opacity: 0.7;
}
.section-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--fg-dim);
  margin-bottom: 0.4rem;
}
.team-embedded {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.team-embedded .panel-tab.active {
  background: var(--accent);
  color: #fff;
  font-weight: 500;
}
.teammate-item {
  display: flex;
  align-items: center;
  padding: 0.7rem 1.2rem;
  border-bottom: 0.5px solid var(--border-light);
  transition: background 0.15s ease;
}
.teammate-item:hover { background: var(--bg-thinking); }
.teammate-info {
  flex: 1;
  min-width: 0;
}
.teammate-name {
  font-weight: 500;
  color: var(--fg);
  margin-right: 0.5rem;
}
.teammate-role {
  font-size: 0.8rem;
  color: var(--fg-dim);
  margin-right: 0.5rem;
}
.teammate-status {
  font-size: 0.8rem;
}
.teammate-dismiss {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.9rem;
  opacity: 0.4;
  transition: opacity 0.2s;
  padding: 4px;
}
.teammate-dismiss:hover { opacity: 1; }
.bb-clear-bar {
  display: flex;
  justify-content: flex-end;
  padding: 0.4rem 1.2rem;
  border-bottom: 0.5px solid var(--border-light);
}
.bb-clear-btn {
  border: none;
  background: none;
  color: var(--fg-dim);
  font-size: 0.78rem;
  cursor: pointer;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  transition: all 0.15s;
}
.bb-clear-btn:hover {
  color: #e74c3c;
  background: var(--bg-thinking);
}
.bb-item {
  padding: 0.7rem 1.2rem;
  border-bottom: 0.5px solid var(--border-light);
}
.bb-key {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 500;
  color: var(--fg);
  cursor: pointer;
  font-size: 0.9rem;
}
.bb-author {
  font-size: 0.75rem;
  color: var(--fg-dim);
  font-weight: 400;
}
.bb-toggle {
  font-size: 0.7rem;
  color: var(--fg-dim);
}
.bb-value-preview {
  font-size: 0.82rem;
  color: var(--fg-dim);
  margin-top: 0.3rem;
  white-space: pre-wrap;
  word-break: break-all;
}
.bb-value-full {
  font-size: 0.82rem;
  color: var(--fg);
  margin-top: 0.3rem;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
}
.team-embedded-wrap {
  position: static !important;
  background: transparent !important;
  inset: auto !important;
  z-index: auto !important;
  display: flex !important;
  height: 100% !important;
  align-items: stretch !important;
  justify-content: flex-end !important;
}
.team-embedded-inner {
  position: static !important;
  width: 100% !important;
  height: 100% !important;
  max-height: 100% !important;
  border-radius: 0 !important;
  box-shadow: none !important;
}
.team-embedded-inner .team-header { display: none; }
.team-embedded-inner .team-close { display: none; }

.team-overlay-embedded {
    position: static !important;
    background: transparent !important;
    inset: auto !important;
    z-index: auto !important;
    display: flex !important;
    height: 100% !important;
    pointer-events: auto !important;
}
.team-panel-embedded {
    position: static !important;
    width: 100% !important;
    height: 100% !important;
    max-height: 100% !important;
    border-radius: 0 !important;
    box-shadow: none !important;
    overflow-y: auto !important;
}
.team-panel-embedded .team-header,
.team-panel-embedded .team-close {
    display: none;
}
</style>
