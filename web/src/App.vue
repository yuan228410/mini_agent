<script setup lang="ts">
import { ref, onMounted, nextTick, computed } from 'vue'
import { initTheme, setTheme, type Theme } from './theme'
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
import WorkflowPanel from './components/WorkflowPanel.vue'
import PlanArtifactCard from './components/PlanArtifactCard.vue'
import { isFinalPlan } from './plan/interactions'
import type { PlanState } from './plan/types'

const SIDEBAR_KEY = 'mini-ai-sidebar-open'
const SIDEBAR_WIDTH_KEY = 'mini-ai-sidebar-width'

const theme = ref<Theme>('linen-light')
const config = ref({
  model: '?',
  context_length: 128000,
  prompt_tokens: 0,
  completion_tokens: 0,
  history_count: 0,
  session_id: '',
  username: '',
})
const sidebarWidth = ref(260)
const sidebarCollapsed = ref(false)
const rightPanelTab = ref<string>('files')
const rightPanelCollapsed = ref(true)
const rightPanelWidth = ref(300)

// 用户相关
const currentUsername = ref('')
const needUsername = ref(false)
const usernameInput = ref('')

// 会话相关
const sessionId = ref('')
const planState = ref<PlanState>('idle')
const todosContent = ref('')
const currentPlan = ref<any>(null)
const activeWorkspace = ref('')

// 组件引用
const chatViewRef = ref<InstanceType<typeof ChatView>>()
const sidebarRef = ref<InstanceType<typeof SessionSidebar>>()

// 当前会话的工作流状态
const currentWorkflowState = computed(() => {
  const state = chatViewRef.value?.getCurrentWorkflowState()
  console.log('[App] currentWorkflowState computed:', state)
  return state
})
const finalPlan = computed(() => isFinalPlan(currentPlan.value) ? currentPlan.value : null)

function startSidebarResize(e: MouseEvent) {
  const startX = e.clientX
  const startWidth = sidebarWidth.value
  function onMove(ev: MouseEvent) {
    const delta = ev.clientX - startX
    const w = startWidth + delta
    if (w < 60) {
      sidebarCollapsed.value = true
    } else {
      sidebarCollapsed.value = false
      sidebarWidth.value = Math.max(160, Math.min(320, w))
    }
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    if (!sidebarCollapsed.value) {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth.value))
    }
    localStorage.setItem(SIDEBAR_KEY, String(!sidebarCollapsed.value))
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

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


onMounted(async () => {
  theme.value = initTheme()
  const saved = localStorage.getItem(SIDEBAR_KEY)
  if (saved !== null) sidebarCollapsed.value = saved === 'false'

  const savedWidth = localStorage.getItem(SIDEBAR_WIDTH_KEY)
  if (savedWidth !== null) sidebarWidth.value = parseInt(savedWidth) || 260

  if (hasUsername()) {
    currentUsername.value = getUsername()
    needUsername.value = false
  } else {
    needUsername.value = true
  }
})

function onSelectTheme(nextTheme: Theme) {
  theme.value = setTheme(nextTheme)
}

function onConfigUpdate(c: any) {
  config.value = { ...config.value, ...c }
  if (c.session_id) sessionId.value = c.session_id
  if (c.plan_state !== undefined) planState.value = c.plan_state
}

function onPlanModeChange(state: PlanState | boolean) {
  planState.value = typeof state === 'boolean' ? (state ? 'planning' : 'idle') : state
}

function onPlanUpdate(plan: any) {
  currentPlan.value = plan
}


function renderTodos() {
  if (!todosContent.value) return ''
  return todosContent.value.split('\n').map(line => {
    if (line.includes('← 当前')) return `<div class="todo-item todo-active">${line}</div>`
    if (line.startsWith('[x]')) return `<div class="todo-item todo-done">${line}</div>`
    return `<div class="todo-item">${line}</div>`
  }).join('')
}

function onModelSwitched(data?: { activeName: string; activeModel: string }) {
  if (data) {
    config.value.model = data.activeModel
  }
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
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem(SIDEBAR_KEY, String(!sidebarCollapsed.value))
}

function onUseSkill(name: string) {
  chatViewRef.value?.useSkill(name)
}

async function onWsCreated(name: string) {
  activeWorkspace.value = name
  // 刷新侧栏工作空间和会话列表
  if (sidebarRef.value) {
    await sidebarRef.value.loadSessions()
  }
}

function submitUsername() {
  const name = usernameInput.value.trim()
  if (!name) return
  setUsername(name)
  currentUsername.value = name
  needUsername.value = false
}

function logout() {
  localStorage.removeItem('mini-ai-username')
  closeWs()
  currentUsername.value = ''
  needUsername.value = true
}

function onTodosUpdate(content: string) {
  todosContent.value = content
}

function openPlanOptionsFromPanel() {
  chatViewRef.value?.openPlanOptionsDialog()
}

function openPlanDecisionFromPanel(payload: any) {
  chatViewRef.value?.openPlanDecisionDialog(payload)
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
        <button class="sidebar-toggle" @click="toggleSidebar" :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'">
          <span class="toggle-icon">{{ sidebarCollapsed ? '▶' : '☰' }}</span>
        </button>
        <h1 class="brand">mini_ai</h1>
        <span class="username-badge">{{ currentUsername }}</span>
        <button class="logout-btn" @click="logout" title="退出登录">↪</button>
      </div>
      <div class="header-right">
        <ModelSelector :session-id="sessionId" :workspace="activeWorkspace || undefined" @switched="onModelSwitched" />
        <ThemeToggle :theme="theme" @select="onSelectTheme" />
      </div>
    </header>
    <div class="main-area">
      <SessionSidebar
        ref="sidebarRef"
        :width="sidebarWidth"
        :collapsed="sidebarCollapsed"
        @switch-session="onSwitchSession"
        @status-change="onStatusChange"
        @toggle="toggleSidebar"
        @workspace-change="onWorkspaceChange"
      />
      <div class="sidebar-resize-handle" @mousedown.prevent="startSidebarResize"></div>
      <ChatView ref="chatViewRef" :workspace="activeWorkspace" @config-update="onConfigUpdate" @status-change="onStatusChange" @plan-mode-change="onPlanModeChange" @plan-update="onPlanUpdate" @todos-update="onTodosUpdate" />
      <div class="rp-resize-handle" @mousedown.prevent="startRightPanelResize"></div>
      <div class="right-panel" :class="{ 'rp-collapsed': rightPanelCollapsed }" :style="rightPanelCollapsed ? {} : { width: rightPanelWidth + 'px' }">
        <div class="rp-tabs">
          <button class="rp-tab" :class="{ active: rightPanelTab === 'plan' }" @click="rightPanelTab = rightPanelTab === 'plan' ? '' : 'plan'; rightPanelCollapsed = false" v-if="finalPlan" title="计划">计划</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'todos' }" @click="rightPanelTab = rightPanelTab === 'todos' ? '' : 'todos'; rightPanelCollapsed = false" v-if="todosContent" title="任务计划">任务</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'workflow' }" @click="rightPanelTab = rightPanelTab === 'workflow' ? '' : 'workflow'; rightPanelCollapsed = false" title="工作流">流程</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'team' }" @click="rightPanelTab = rightPanelTab === 'team' ? '' : 'team'; rightPanelCollapsed = false" title="协作">协作</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'skills' }" @click="rightPanelTab = rightPanelTab === 'skills' ? '' : 'skills'; rightPanelCollapsed = false" title="工具">工具</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'files' }" @click="rightPanelTab = rightPanelTab === 'files' ? '' : 'files'; rightPanelCollapsed = false" title="文件">文件</button>
          <button class="rp-tab" :class="{ active: rightPanelTab === 'settings' }" @click="rightPanelTab = rightPanelTab === 'settings' ? '' : 'settings'; rightPanelCollapsed = false" title="设置">设置</button>
          <button class="rp-tab rp-collapse" @click="rightPanelCollapsed = !rightPanelCollapsed">{{ rightPanelCollapsed ? '◂' : '▸' }}</button>
        </div>
        <div class="rp-body" v-show="!rightPanelCollapsed">
          <div v-if="rightPanelTab === 'plan' && finalPlan" class="rp-content">
            <div class="rp-title">最终执行计划</div>
            <PlanArtifactCard :plan="finalPlan" @open-option="openPlanOptionsFromPanel" @open-decision="openPlanDecisionFromPanel" />
          </div>
          <div v-if="rightPanelTab === 'todos'" class="rp-content">
            <div class="rp-title">任务计划</div>
            <div class="rp-todos" v-html="renderTodos()"></div>
          </div>
          <div v-if="rightPanelTab === 'workflow'" class="rp-content">
            <WorkflowPanel embedded :session-workflow-state="currentWorkflowState" />
          </div>

          <div v-if="rightPanelTab === 'team'" class="rp-content">
            <TeamPanel :username="currentUsername" :workspace="activeWorkspace" embedded />
          </div>
          <div v-if="rightPanelTab === 'skills'" class="rp-content">
            <SkillPanel :username="currentUsername" :workspace="activeWorkspace" embedded @use="onUseSkill" />
          </div>
          <div v-if="rightPanelTab === 'files'" class="rp-content rp-content-fill">
            <FileBrowserPanel :workspace="activeWorkspace" @workspace-created="onWsCreated" embedded />
          </div>
          <div v-if="rightPanelTab === 'settings'" class="rp-content">
            <SettingsPanel embedded />
          </div>
        </div>
      </div>
    </div>
    <StatusBar v-bind="config" :plan-state="planState" />
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
  padding: 0.85rem 1.35rem;
  border-bottom: 1px solid var(--surface-hairline);
  background: linear-gradient(180deg, color-mix(in srgb, var(--bg) 86%, transparent), color-mix(in srgb, var(--bg-card) 66%, transparent));
  backdrop-filter: blur(22px) saturate(1.12);
  box-shadow: 0 1px 0 rgba(255,255,255,.035);
  flex-shrink: 0;
  position: relative;
  z-index: 20;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

.sidebar-toggle {
  width: 36px;
  height: 36px;
  border: 1px solid var(--surface-hairline);
  border-radius: 14px;
  background: var(--surface-control);
  color: var(--fg-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform .16s var(--ease-out), border-color .16s, color .16s, box-shadow .16s;
  font-size: 0.9rem;
  box-shadow: 0 8px 24px color-mix(in srgb, var(--shadow) 18%, transparent), inset 0 1px 0 rgba(255,255,255,.04);
}

.sidebar-toggle:hover {
  border-color: color-mix(in srgb, var(--accent) 46%, var(--border));
  color: var(--accent);
  transform: translateY(-1px);
  box-shadow: var(--glow-accent);
}

.brand {
  font-family: var(--font-display);
  font-size: 1.38rem;
  font-weight: 800;
  color: var(--fg);
  letter-spacing: -0.02em;
  text-shadow: 0 10px 28px color-mix(in srgb, var(--shadow) 44%, transparent);
}

.brand::after {
  content: '.';
  color: var(--accent);
  filter: drop-shadow(0 0 12px var(--accent-glow));
}

.username-badge {
  font-size: 0.72rem;
  color: var(--fg-muted);
  font-family: var(--font-mono);
  font-weight: 700;
  padding: 0.28rem 0.65rem;
  border: 1px solid var(--surface-hairline);
  border-radius: 999px;
  background: color-mix(in srgb, var(--bg-card) 72%, transparent);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.035);
}

.logout-btn {
  width: 36px;
  height: 36px;
  border: 1px solid var(--surface-hairline);
  border-radius: 14px;
  background: var(--surface-control);
  cursor: pointer;
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all .16s var(--ease-out);
  color: var(--fg-muted);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.035);
}

.logout-btn:hover {
  border-color: #e55;
  color: #e55;
  background: color-mix(in srgb, #e55 12%, var(--bg-card));
  transform: translateY(-1px);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}



.main-area {
  flex: 1;
  display: flex;
  overflow: hidden;
  background: linear-gradient(135deg, color-mix(in srgb, var(--bg) 72%, transparent), transparent 42%);
  position: relative;
}

.main-area::before {
  content: '';
  position: absolute;
  inset: 10px;
  pointer-events: none;
  border: 1px solid color-mix(in srgb, var(--border-light) 26%, transparent);
  border-radius: 28px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.035);
}

.main-area > * {
  position: relative;
  z-index: 1;
}

.sidebar-resize-handle {
  width: 6px;
  cursor: col-resize;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--border) 38%, transparent), transparent);
  flex-shrink: 0;
  z-index: 10;
  transition: background .16s ease, opacity .16s ease;
}
.sidebar-resize-handle:hover {
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--accent) 56%, transparent), transparent);
  opacity: 0.75;
}

.rp-resize-handle {
  width: 6px;
  cursor: col-resize;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--border) 38%, transparent), transparent);
  flex-shrink: 0;
  z-index: 10;
  transition: background .16s ease, opacity .16s ease;
}
.rp-resize-handle:hover {
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--accent) 56%, transparent), transparent);
  opacity: 0.75;
}

.right-panel {
  width: 300px;
  flex-shrink: 0;
  border-left: 1px solid var(--surface-hairline);
  background: var(--surface-panel);
  box-shadow: -12px 0 42px color-mix(in srgb, var(--shadow) 22%, transparent);
  backdrop-filter: blur(18px) saturate(1.08);
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
  border-bottom: 1px solid var(--surface-hairline);
  padding: 0.55rem 0.55rem;
  gap: 6px;
  flex-shrink: 0;
  background: linear-gradient(180deg, color-mix(in srgb, var(--bg-card) 50%, transparent), transparent);
}

.rp-tab {
  height: 32px;
  min-width: 32px;
  padding: 0 10px;
  border: 1px solid var(--surface-hairline);
  background: color-mix(in srgb, var(--bg-card) 58%, transparent);
  cursor: pointer;
  font-size: 0.76rem;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all .16s var(--ease-out);
  position: relative;
  white-space: nowrap;
  color: var(--fg-muted);
  font-family: var(--font-mono);
  font-weight: 800;
}

.rp-tab:hover {
  background: var(--bg-hover);
  border-color: color-mix(in srgb, var(--accent) 34%, var(--border));
  color: var(--fg);
  transform: translateY(-1px);
}

.rp-tab.active {
  background: var(--surface-active);
  border-color: color-mix(in srgb, var(--accent) 46%, var(--border));
  color: var(--accent);
  box-shadow: 0 0 18px color-mix(in srgb, var(--accent-glow) 20%, transparent);
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
  padding: 0;
  font-size: 0.78rem;
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
