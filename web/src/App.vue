<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { initTheme, toggleTheme, type Theme } from './theme'
import { hasUsername, getUsername, setUsername } from './api'
import ChatView from './components/ChatView.vue'
import SessionSidebar from './components/SessionSidebar.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import StatusBar from './components/StatusBar.vue'
import ModelSelector from './components/ModelSelector.vue'
import SkillPanel from './components/SkillPanel.vue'
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
  system_prompt_chars: 0,
  history_count: 0,
  session_id: '',
  username: '',
})
const showSidebar = ref(true)
const showSkills = ref(false)
const showFiles = ref(false)
const showSettings = ref(false)
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
</script>

<template>
  <div v-if="needUsername" class="username-screen">
    <div class="username-card">
      <div class="username-icon">m</div>
      <h2 class="username-title">mini_ai</h2>
      <p class="username-sub">输入用户名开始对话</p>
      <div class="username-input-wrap">
        <input
          v-model="usernameInput"
          class="username-input"
          placeholder="用户名"
          autofocus
          @keydown.enter="submitUsername"
        />
        <button class="username-btn" :disabled="!usernameInput.trim()" @click="submitUsername">
          进入
        </button>
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
      </div>
      <div class="header-right">
        <ModelSelector :session-id="sessionId" :workspace="activeWorkspace || undefined" @switched="onModelSwitched" />
        <button class="skill-btn" @click="showFiles = true" title="文件浏览">
          <span>📄</span>
        </button>
        <button class="skill-btn" @click="showSkills = true" title="工具面板">
          <span>🔧</span>
        </button>
        <button class="skill-btn" @click="showSettings = true" title="设置">
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
      <div class="todos-sidebar" v-if="todosContent">
        <div class="todos-sidebar-header" @click="todosCollapsed = !todosCollapsed">
          📋 任务计划
          <span class="todos-toggle">{{ todosCollapsed ? '▸' : '▾' }}</span>
        </div>
        <div class="todos-sidebar-body" v-if="!todosCollapsed" v-html="renderTodos()"></div>
      </div>
    </div>
    <StatusBar v-bind="config" :plan-mode="planMode" />
    <SkillPanel :visible="showSkills" :username="currentUsername" :workspace="activeWorkspace" @close="showSkills = false" @use="onUseSkill" />
    <FileBrowserPanel :visible="showFiles" :workspace="activeWorkspace" @close="showFiles = false" />
    <SettingsPanel :visible="showSettings" @close="showSettings = false" />
  </template>
</template>

<style scoped>
.username-screen {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
}

.username-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 3rem;
}

.username-icon {
  width: 72px;
  height: 72px;
  border-radius: 18px;
  background: var(--accent);
  color: var(--bg);
  font-family: 'Playfair Display', serif;
  font-weight: 700;
  font-size: 2.2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1.5rem;
}

.username-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--fg);
  margin-bottom: 0.5rem;
}

.username-title::after {
  content: '.';
  color: var(--accent);
}

.username-sub {
  color: var(--fg-muted);
  font-size: 0.95rem;
  margin-bottom: 2rem;
}

.username-input-wrap {
  display: flex;
  gap: 0.6rem;
  width: 320px;
}

.username-input {
  flex: 1;
  font-family: 'Source Sans 3', sans-serif;
  font-size: 1rem;
  padding: 0.6rem 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-input);
  color: var(--fg);
  outline: none;
  transition: border-color 0.2s ease;
}

.username-input:focus {
  border-color: var(--accent);
}

.username-btn {
  padding: 0.6rem 1.2rem;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font-size: 0.95rem;
  cursor: pointer;
  transition: background 0.2s ease;
}

.username-btn:hover:not(:disabled) {
  background: var(--accent-hover);
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

.main-area {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.todos-sidebar {
  width: 260px;
  flex-shrink: 0;
  border-left: 0.5px solid var(--border);
  background: var(--bg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.todos-sidebar-header {
  padding: 0.8rem 1rem;
  font-weight: 600;
  font-size: 0.82rem;
  cursor: pointer;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 0.3rem;
  color: var(--accent, #E8912D);
  border-bottom: 0.5px solid var(--border);
}

.todos-toggle {
  font-size: 0.7rem;
  margin-left: auto;
}

.todos-sidebar-body {
  flex: 1;
  overflow-y: auto;
  padding: 0.6rem 1rem;
  font-family: 'Source Sans 3', sans-serif;
}

.todos-sidebar-body :deep(.todo-item) {
  font-size: 0.8rem;
  padding: 2px 0;
  line-height: 1.5;
}

.todos-sidebar-body :deep(.todo-active) {
  font-weight: 600;
  color: var(--accent, #E8912D);
}

.todos-sidebar-body :deep(.todo-done) {
  opacity: 0.5;
  text-decoration: line-through;
}
</style>
