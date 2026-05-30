<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { initTheme, toggleTheme, type Theme } from './theme'
import { hasUsername, getUsername, setUsername, closeWs } from './api'
import ChatView from './components/ChatView.vue'
import SessionSidebar from './components/SessionSidebar.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import StatusBar from './components/StatusBar.vue'
import ModelSelector from './components/ModelSelector.vue'
import SkillPanel from './components/SkillPanel.vue'
import TeamPanel from './components/TeamPanel.vue'
import FileBrowserPanel from './components/FileBrowserPanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'

const SIDEBAR_KEY = 'mini-ai-sidebar-open'

const theme = ref<Theme>('light')
const config = ref({
  version: '',
  model: '?',
  context_length: 128000,
  prompt_tokens: 0,
  completion_tokens: 0,
  system_prompt_tokens: 0,
  history_count: 0,
  session_id: '',
  username: '',
})
const showSidebar = ref(true)
const rightPanelTab = ref<string>('files')
const rightPanelCollapsed = ref(false)
const rightPanelWidth = ref(300)

function startRightPanelResize(e: MouseEvent) {
  const startX = e.clientX
  const startWidth = rightPanelWidth.value
  function onMove(ev: MouseEvent) {
    const delta = startX - ev.clientX
    const w = startWidth + delta
    if (w < 60) {
      rightPanelCollapsed.value = true
    } else {
      rightPanelCollapsed.value = false
      rightPanelWidth.value = Math.max(160, Math.min(600, w))
    }
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
const activeWorkspace = ref('')
const chatViewRef = ref<InstanceType<typeof ChatView>>()
const sidebarRef = ref<InstanceType<typeof SessionSidebar>>()
const needUsername = ref(false)
const usernameInput = ref('')
const currentUsername = ref('')
const sessionId = ref('')
const planMode = ref(false)
const todosContent = ref('')
const todosCollapsed = ref(false)

onMounted(async () => {
  theme.value = initTheme()
  const saved = localStorage.getItem(SIDEBAR_KEY)
  if (saved !== null) showSidebar.value = saved === 'true'

  if (hasUsername()) {
    currentUsername.value = getUsername()
    needUsername.value = false
  } else {
    needUsername.value = true
  }
})

function onToggleTheme() {
  theme.value = toggleTheme(theme.value)
}

function onConfigUpdate(c: any) {
  config.value = { ...config.value, ...c }
  if (c.session_id) sessionId.value = c.session_id
  if (c.plan_mode !== undefined) planMode.value = c.plan_mode
}

function onPlanModeChange(mode: boolean) {
  planMode.value = mode
}

function onTodosUpdate(content: string) {
  todosContent.value = content
}

function renderTodos() {
  if (!todosContent.value) return ''
  return todosContent.value.split('\n').map(line => {
    if (line.includes('← 当前')) return `<div class="todo-item todo-active">${line}</div>`
    if (line.startsWith('[x]')) return `<div class="todo-item todo-done">${line}</div>`
    return `<div class="todo-item">${line}</div>`
  }).join('')
}

function onModelSwitched() {
  config.value.model = '(switched)'
}

async function onWorkspaceChange(wsName: string | null) {
  activeWorkspace.value = wsName || ''
}

async function onSwitchSession(sid: string, wsName: string | null) {
  activeWorkspace.value = wsName || ''
  await nextTick()
  chatViewRef.value?.switchToSession(sid, wsName || undefined)
  if (sidebarRef.value) sidebarRef.value.setActiveSession(sid)
}

function onStatusChange(sid: string, status: 'idle' | 'generating') {
  if (sidebarRef.value) sidebarRef.value.updateSessionStatus(sid, status)
}

function toggleSidebar() {
  showSidebar.value = !showSidebar.value
  localStorage.setItem(SIDEBAR_KEY, String(showSidebar.value))
}

function onUseSkill(name: string) {
  chatViewRef.value?.useSkill(name)
}

function submitUsername() {
  const name = usernameInput.value.trim()
  if (!name) return
  setUsername(name)
  currentUsername.value = name
  needUsername.value = false
}

function logout() {
  setUsername('')
  currentUsername.value = ''
  needUsername.value = true
  closeWs()
  sessionId.value = ''
}
</script>

<template>
  <div v-if="needUsername" class="username-screen">
    <div class="username-card">
      <div class="username-card-left">
        <div class="username-logo-wrap">
          <div class="username-icon">m</div>
        </div>
        <h2 class="username-title">mini_ai</h2>
        <p class="username-tagline">你的 AI 编程伙伴</p>
        <div class="username-features">
          <div class="feature-item">
            <span class="feature-icon">🤖</span>
            <span class="feature-text">多模型对话，灵活切换</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">🔧</span>
            <span class="feature-text">工具调用 · MCP · 技能系统</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">👥</span>
            <span class="feature-text">多 Agent 协作，团队模式</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">📂</span>
            <span class="feature-text">工作空间管理，项目隔离</span>
          </div>
        </div>
      </div>
      <div class="username-card-right">
        <div class="username-form-wrap">
          <p class="username-sub">开始使用 mini_ai</p>
          <div class="username-input-wrap">
            <input
              v-model="usernameInput"
              class="username-input"
              placeholder="输入用户名"
              autofocus
              @keydown.enter="submitUsername"
            />
          </div>
          <button class="username-btn" :disabled="!usernameInput.trim()" @click="submitUsername">
            进入
          </button>
        </div>
      </div>
    </div>
  </div>
  <template v-else>
    <header class="app-header">
      <div class="header-left">
        <button class="sidebar-toggle" @click="toggleSidebar" :title="showSidebar ? '收起侧栏' : '展开侧栏'">
          <span class="toggle-icon">{{ showSidebar ? '☰' : '☰' }}</span>
        </button>
        <h1 class="brand">mini_ai</h1>
        <span class="username-badge">{{ currentUsername }}</span>
        <button class="logout-btn" @click="logout" title="退出登录">↪</button>
      </div>
      <div class="header-right">
        <ModelSelector :session-id="sessionId" :workspace="activeWorkspace || undefined" @switched="onModelSwitched" />
        <button class="skill-btn" :class="{ active: rightPanelTab === 'files' }" @click="rightPanelTab = rightPanelTab === 'files' ? '' : 'files'" title="文件浏览">
          <span>📄</span>
        </button>
        <button class="skill-btn" :class="{ active: rightPanelTab === 'team' }" @click="rightPanelTab = rightPanelTab === 'team' ? '' : 'team'" title="协作面板">
          <span>👥</span>
        </button>
        <button class="skill-btn" :class="{ active: rightPanelTab === 'skills' }" @click="rightPanelTab = rightPanelTab === 'skills' ? '' : 'skills'" title="工具面板">
          <span>🔧</span>
        </button>
        <button class="skill-btn" :class="{ active: rightPanelTab === 'settings' }" @click="rightPanelTab = rightPanelTab === 'settings' ? '' : 'settings'" title="设置">
          <span>⚙</span>
        </button>
        <ThemeToggle :theme="theme" @toggle="onToggleTheme" />
      </div>
    </header>
    <div class="main-area">
      <SessionSidebar
        ref="sidebarRef"
        :visible="showSidebar"
        @switch-session="onSwitchSession"
        @status-change="onStatusChange"
        @toggle="toggleSidebar"
        @workspace-change="onWorkspaceChange"
      />
      <ChatView ref="chatViewRef" :workspace="activeWorkspace" @config-update="onConfigUpdate" @status-change="onStatusChange" @plan-mode-change="onPlanModeChange" @todos-update="onTodosUpdate" />
      <div class="rp-resize-handle" @mousedown.prevent="startRightPanelResize"></div>
      <div class="right-panel" :class="{ 'rp-collapsed': rightPanelCollapsed }" :style="rightPanelCollapsed ? {} : { width: rightPanelWidth + 'px' }">
        <div class="rp-tabs">
          <button class="rp-tab" :class="{ active: rightPanelTab === 'todos' }" @click="rightPanelTab = rightPanelTab === 'todos' ? '' : 'todos'" v-if="todosContent">📋</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'team' }" @click="rightPanelTab = rightPanelTab === 'team' ? '' : 'team'">👥</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'skills' }" @click="rightPanelTab = rightPanelTab === 'skills' ? '' : 'skills'">🔧</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'files' }" @click="rightPanelTab = rightPanelTab === 'files' ? '' : 'files'">📄</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'settings' }" @click="rightPanelTab = rightPanelTab === 'settings' ? '' : 'settings'">⚙</button>
          <button class="rp-tab rp-collapse" @click="rightPanelCollapsed = !rightPanelCollapsed">{{ rightPanelCollapsed ? '◂' : '▸' }}</button>
        </div>
        <div class="rp-body" v-show="!rightPanelCollapsed">
          <div v-if="rightPanelTab === 'todos'" class="rp-content">
            <div class="rp-title">📋 任务计划</div>
            <div class="rp-todos" v-html="renderTodos()"></div>
          </div>
          <div v-if="rightPanelTab === 'team'" class="rp-content">
            <TeamPanel :username="currentUsername" :workspace="activeWorkspace" embedded />
          </div>
          <div v-if="rightPanelTab === 'skills'" class="rp-content">
            <SkillPanel :username="currentUsername" :workspace="activeWorkspace" embedded @use="onUseSkill" />
          </div>
          <div v-if="rightPanelTab === 'files'" class="rp-content rp-content-fill">
            <FileBrowserPanel :workspace="activeWorkspace" embedded />
          </div>
          <div v-if="rightPanelTab === 'settings'" class="rp-content">
            <SettingsPanel embedded />
          </div>
        </div>
      </div>
    </div>
    <StatusBar v-bind="config" :plan-mode="planMode" />
  </template>
</template>

<style scoped>
.username-screen {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
  position: relative;
  overflow: hidden;
}

.username-screen::before {
  content: '';
  position: absolute;
  width: 600px;
  height: 600px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--accent-soft) 0%, transparent 70%);
  top: -250px;
  right: -150px;
  pointer-events: none;
}

.username-screen::after {
  content: '';
  position: absolute;
  width: 500px;
  height: 500px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--accent-soft) 0%, transparent 70%);
  bottom: -200px;
  left: -100px;
  pointer-events: none;
}

.username-card {
  display: flex;
  background: var(--bg-card);
  border: 0.5px solid var(--border);
  border-radius: 16px;
  box-shadow: 0 8px 40px rgba(0,0,0,0.06), 0 2px 8px rgba(0,0,0,0.03);
  overflow: hidden;
  position: relative;
  z-index: 1;
  min-width: 640px;
  animation: scaleIn 0.4s ease-out;
}

.username-card-left {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 3rem 2.5rem;
  flex: 1;
}

.username-card-right {
  display: flex;
  align-items: center;
  padding: 3rem 2.5rem;
  background: var(--bg);
  border-left: 0.5px solid var(--border);
  min-width: 260px;
}

.username-logo-wrap {
  margin-bottom: 1.2rem;
}

.username-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  background: var(--accent);
  color: var(--bg);
  font-family: 'Playfair Display', serif;
  font-weight: 700;
  font-size: 1.8rem;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 20px rgba(232, 145, 45, 0.25);
}

.username-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--fg);
  margin: 0 0 0.3rem 0;
}

.username-title::after {
  content: '.';
  color: var(--accent);
}

.username-tagline {
  color: var(--fg-muted);
  font-size: 0.9rem;
  margin: 0 0 2rem 0;
  font-style: italic;
  letter-spacing: 0.02em;
}

.username-features {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 0.9rem;
  color: var(--fg);
}

.feature-icon {
  font-size: 1.1rem;
  flex-shrink: 0;
}

.feature-text {
  color: var(--fg-muted);
}

.username-form-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
}

.username-sub {
  color: var(--fg-muted);
  font-size: 0.95rem;
  margin: 0 0 1.5rem 0;
  text-align: center;
}

.username-input-wrap {
  width: 100%;
  margin-bottom: 1rem;
}

.username-input {
  width: 100%;
  box-sizing: border-box;
  font-family: 'Source Sans 3', sans-serif;
  font-size: 1rem;
  padding: 0.7rem 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-input);
  color: var(--fg);
  outline: none;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.username-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.username-input::placeholder {
  color: var(--fg-dim);
  opacity: 0.6;
}

.username-btn {
  width: 100%;
  padding: 0.7rem 1.4rem;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.15s ease, box-shadow 0.2s ease;
  position: relative;
  overflow: hidden;
}

.username-btn::after {
  content: ' →';
  opacity: 0;
  transition: opacity 0.2s ease, margin-left 0.2s ease;
}

.username-btn:hover:not(:disabled) {
  background: var(--accent-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(232, 145, 45, 0.3);
}

.username-btn:hover:not(:disabled)::after {
  opacity: 1;
}

.username-btn:active:not(:disabled) {
  transform: translateY(0);
}

.username-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.8rem 1.5rem;
  border-bottom: 0.5px solid var(--border);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

.sidebar-toggle {
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  font-size: 0.9rem;
}

.sidebar-toggle:hover {
  border-color: var(--accent);
  background: var(--bg-thinking);
}

.brand {
  font-family: 'Playfair Display', serif;
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--fg);
  letter-spacing: -0.02em;
}

.brand::after {
  content: '.';
  color: var(--accent);
}

.username-badge {
  font-size: 0.78rem;
  color: var(--fg-dim);
  font-family: 'JetBrains Mono', monospace;
  padding: 0.15rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 4px;
}

.logout-btn {
  width: 28px;
  height: 28px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  color: var(--fg-dim);
}

.logout-btn:hover {
  border-color: #e55;
  color: #e55;
  background: rgba(238, 85, 85, 0.08);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

.skill-btn {
  width: 36px;
  height: 36px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-card);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  font-size: 0.95rem;
}

.skill-btn:hover {
  border-color: var(--accent);
  background: var(--bg-thinking);
}

.skill-btn.active {
  border-color: var(--accent);
  background: var(--bg-thinking);
}

.main-area {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.rp-resize-handle {
  width: 4px;
  cursor: col-resize;
  background: transparent;
  flex-shrink: 0;
  z-index: 10;
}
.rp-resize-handle:hover {
  background: var(--accent);
  opacity: 0.3;
}

.right-panel {
  width: 300px;
  flex-shrink: 0;
  border-left: 0.5px solid var(--border);
  background: var(--bg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.2s ease;
}

.right-panel:not(.rp-collapsed) .rp-body:empty {
  display: none;
}

.rp-tabs {
  display: flex;
  align-items: center;
  border-bottom: 0.5px solid var(--border);
  padding: 0.35rem 0.4rem;
  gap: 4px;
  flex-shrink: 0;
}

.rp-tab {
  width: 30px;
  height: 30px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  font-size: 0.85rem;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  position: relative;
}

.rp-tab:hover {
  background: var(--bg-card);
  border-color: var(--border);
}

.rp-tab.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.rp-tab.active::after {
  content: '';
  position: absolute;
  bottom: -5px;
  left: 50%;
  transform: translateX(-50%);
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
}

.rp-collapse { margin-left: auto; font-size: 0.7rem; color: var(--fg-dim); }
.rp-collapse:hover { color: var(--fg); background: var(--bg-card); border-color: var(--border); }

.right-panel.rp-collapsed {
  width: 40px;
}
.right-panel.rp-collapsed .rp-tabs {
  flex-direction: column;
  border-bottom: none;
  padding: 0.4rem 0;
  gap: 4px;
}
.right-panel.rp-collapsed .rp-tab {
  width: 30px;
  height: 30px;
  font-size: 0.8rem;
}
.right-panel.rp-collapsed .rp-tab.active::after {
  bottom: auto;
  right: -5px;
  left: auto;
  top: 50%;
  transform: translateY(-50%);
}

.rp-body {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.rp-content {
  padding: 0.6rem;
  flex-shrink: 0;
}

.rp-title {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--accent);
  margin-bottom: 0.5rem;
  padding: 0.2rem 0.4rem;
}

.rp-todos {
  font-family: 'Source Sans 3', sans-serif;
  font-size: 0.8rem;
}

.rp-todos :deep(.todo-item) {
  padding: 2px 0;
  line-height: 1.5;
}

.rp-todos :deep(.todo-active) {
  font-weight: 600;
  color: var(--accent, #E8912D);
}

.rp-todos :deep(.todo-done) {
  opacity: 0.5;
  text-decoration: line-through;
}

.rp-content-fill {
  padding: 0 !important;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
