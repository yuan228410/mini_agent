<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed, watch } from 'vue'
import {
  ensureWs, onWsEvent, wsChat, abortChat, closeWs, sendPlan, sendAct,
  getConfig, createSession, getHistory, resetChat, renameSession,
  getSessions, getWorkspaces, exportSession,
  type WsEvent, type HistoryMessage,
} from '../api'
import MessageItem from './MessageItem.vue'
import InputBar from './InputBar.vue'

interface Message {
  role: 'user' | 'assistant'
  content: string

  thinking?: { chars: number; elapsed: number; content: string }
  tools?: { name: string; args: string; result: string; elapsed: number }[]
  streaming?: boolean
  timestamp?: string
}

interface SessionState {
  messages: Message[]
  isStreaming: boolean
  _currentContent: string
  _currentThinking: string
}

const SESSION_KEY = 'mini-ai-session-id'

function _localTs(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return [d.getFullYear(), pad(d.getMonth()+1), pad(d.getDate())].join('-') + 'T' + [pad(d.getHours()), pad(d.getMinutes()), pad(d.getSeconds())].join(':')
}
function _cacheKey(sid: string, ws?: string): string {
  return `${ws || 'default'}:${sid}`
}
const _states = new Map<string, SessionState>()
const activeSessionId = ref('')
const messages = ref<Message[]>([])
const isStreaming = ref(false)
const planMode = ref(false)
const todosContent = ref('')

const chatContainer = ref<HTMLElement>()
const props = defineProps<{ workspace?: string }>()
const emit = defineEmits(['config-update', 'status-change', 'plan-mode-change', 'todos-update'])

let _unsubWs: (() => void) | null = null
let _flushTimer: number | null = null
let _scrollTimer: number | null = null
let _isNearBottom = true
const FLUSH_INTERVAL = 50
const SCROLL_INTERVAL = 100
const BOTTOM_THRESHOLD = 80

function _state(sid: string): SessionState {
  const key = _cacheKey(sid, props.workspace)
  if (!_states.has(key)) {
    _states.set(key, { messages: [], isStreaming: false, _currentContent: '', _currentThinking: '' })
  }
  return _states.get(key)!
}

function _save() {
  const key = _cacheKey(activeSessionId.value, props.workspace)
  const s = _states.get(key)
  if (!s) return
  s.messages = [...messages.value]
  s.isStreaming = isStreaming.value
}

function _load(sid: string) {
  const s = _state(sid)
  if (sid === activeSessionId.value) {
    messages.value = s.messages.map(m => ({ ...m }))
    isStreaming.value = s.isStreaming
  }
}

function _scheduleFlush() {
  if (_flushTimer !== null) return
  _flushTimer = window.setTimeout(() => {
    _flushTimer = null
    _doFlush()
  }, FLUSH_INTERVAL)
}

function _doFlush() {
  const sid = activeSessionId.value
  const key = _cacheKey(sid, props.workspace)
  const s = _states.get(key)
  if (!s) return
  const lastIdx = s.messages.length - 1
  if (lastIdx >= 0) {
    const old = s.messages[lastIdx]
    s.messages[lastIdx] = { ...old, content: s._currentContent }
  }
  messages.value = [...s.messages]
  isStreaming.value = s.isStreaming
}

function _scheduleScroll() {
  if (!_isNearBottom) return
  if (_scrollTimer !== null) return
  _scrollTimer = window.setTimeout(() => {
    _scrollTimer = null
    if (_isNearBottom) scrollToBottom()
  }, SCROLL_INTERVAL)
}

let _initialized = false

onMounted(async () => {
  _unsubWs = onWsEvent(handleWsEvent)
  ensureWs().catch(() => {})
})

watch(() => props.workspace, (ws) => {
  const effectiveWs = ws || 'default'
  if (!_initialized) {
    _initialized = true
    initSession(effectiveWs).catch(() => {})
    fetchConfig().catch(() => {})
  }
})

onUnmounted(() => {
  if (_unsubWs) _unsubWs()
  closeWs()
  if (_flushTimer !== null) { clearTimeout(_flushTimer); _flushTimer = null }
  if (_scrollTimer !== null) { clearTimeout(_scrollTimer); _scrollTimer = null }
})

async function initSession(ws?: string) {
  console.time('[perf] initSession')
  const stored = localStorage.getItem(SESSION_KEY)
  if (stored) {
    activeSessionId.value = stored
    await restoreHistory(stored, ws || props.workspace || 'default')
  } else {
    await newSession()
  }
  preloadAllSessions(ws || props.workspace || 'default')
  console.timeEnd('[perf] initSession')
}

async function preloadAllSessions(ws?: string) {
  console.time('[perf] preloadAllSessions')
  try {
    const resp = await getSessions(ws || undefined)
    const sessions = resp.sessions || []
    for (const s of sessions) {
      if (s.session_id === activeSessionId.value) continue
      const key = _cacheKey(s.session_id, ws)
      if (_states.has(key) && _states.get(key)!.messages.length > 0) continue
      restoreHistory(s.session_id, ws).catch(() => {})
    }
    const resp2 = await getWorkspaces()
    for (const w of resp2.workspaces || []) {
      if (w.name === ws) continue
      const resp3 = await getSessions(w.name)
      for (const s of resp3.sessions || []) {
        const key = _cacheKey(s.session_id, w.name)
        if (_states.has(key) && _states.get(key)!.messages.length > 0) continue
        restoreHistory(s.session_id, w.name).catch(() => {})
      }
    }
  } catch {}
  console.timeEnd('[perf] preloadAllSessions')
}

async function newSession(ws?: string) {
  try {
    const resp = await createSession(ws || props.workspace)
    activeSessionId.value = resp.session_id
    localStorage.setItem(SESSION_KEY, resp.session_id)
    _load(resp.session_id)
  } catch {
    activeSessionId.value = 'default'
    _load('default')
  }
}

async function restoreHistory(sid: string, ws?: string) {
  const _t0 = performance.now()
  try {
    const resp = await getHistory(sid, ws || props.workspace)
    const raw = (resp.history || []).filter((m: any) => m.role !== 'system' && m.role !== 'tool')
    const merged: Message[] = []
    for (const m of raw) {
      if (m.role === 'assistant' && merged.length > 0 && merged[merged.length - 1].role === 'assistant') {
        const prev = merged[merged.length - 1]
        if (m.content) prev.content = (prev.content || '') + m.content
      } else {
        merged.push({ role: m.role as 'user' | 'assistant', content: m.content || '', timestamp: m.timestamp || '' })
      }
    }
    const s = _state(sid)
    s.messages = merged
    _load(sid)
    await nextTick()
    scrollToBottom()
  } catch {
    _state(sid).messages = []
    _load(sid)
  }
}

async function fetchConfig() {
  try {
    const c = await getConfig(activeSessionId.value, props.workspace || 'default')
    emit('config-update', c)
  } catch {}
}

function handleWsEvent(event: WsEvent) {
  const sid = event.data?.session_id || activeSessionId.value
  const s = _state(sid)

  _processEvent(s, event)

  const isTerminal = event.event === 'done' || event.event === 'aborted' || event.event === 'error'

  if (isTerminal) s.isStreaming = false

  if (sid === activeSessionId.value) {
    if (isTerminal) {
      if (_flushTimer !== null) { clearTimeout(_flushTimer); _flushTimer = null }
      _doFlush()
      if (event.data?.prompt_tokens !== undefined) {
        emit('config-update', {
          prompt_tokens: event.data.prompt_tokens,
          completion_tokens: event.data.completion_tokens || 0,
        })
      }
      fetchConfig()
    } else {
      _scheduleFlush()
      if (event.data?.prompt_tokens !== undefined && event.data.prompt_tokens > 0) {
        emit('config-update', {
          prompt_tokens: event.data.prompt_tokens,
          completion_tokens: event.data.completion_tokens || 0,
        })
      }
    }
    _scheduleScroll()
  }

  if (isTerminal) {
    s._currentContent = ''
    s._currentThinking = ''
    emit('status-change', sid, 'idle')
  }
}

function _startNewAssistantMsg(s: SessionState) {
  const last = s.messages[s.messages.length - 1]
  if (!last || last.role !== 'assistant') return
  const hasContent = last.content || (last.tools && last.tools.length) || s._currentContent
  if (!hasContent) return
  s.messages[s.messages.length - 1] = { ...last, streaming: false, content: s._currentContent || last.content }
  s._currentContent = ''
  s._currentThinking = ''
  s.messages.push({ role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs() })
}

function _processEvent(s: SessionState, event: WsEvent) {
  const msg = s.messages[s.messages.length - 1]

  switch (event.event) {
    case 'thinking_start':
      s._currentThinking = ''
      _startNewAssistantMsg(s)
      break
    case 'thinking':
      s._currentThinking += event.data.content || ''
      {
        const m = s.messages[s.messages.length - 1]
        if (m && m.role === 'assistant') {
          m.thinking = { chars: s._currentThinking.length, elapsed: 0, content: s._currentThinking }
        }
      }
      break
    case 'thinking_end':
      {
        const m = s.messages[s.messages.length - 1]
        if (m && m.role === 'assistant' && m.thinking) {
          m.thinking.chars = event.data.chars || m.thinking.chars
          m.thinking.elapsed = event.data.elapsed || 0
        }
      }
      break
    case 'text':
      s._currentContent += event.data.content || ''
      break
    case 'tool_start':
      {
        const m = s.messages[s.messages.length - 1]
        if (m && m.role === 'assistant') {
          if (!m.tools) m.tools = []
          m.tools.push({ name: event.data.name || '?', args: event.data.args || '', result: '...', elapsed: 0 })
        }
      }
      break
    case 'todos':
      todosContent.value = event.data.content || ''
      emit('todos-update', todosContent.value)
      break
    case 'tool_result':
      {
        const m = s.messages[s.messages.length - 1]
        if (m && m.role === 'assistant' && m.tools && m.tools.length > 0) {
          const last = m.tools[m.tools.length - 1]
          last.result = event.data.result || ''
          last.elapsed = event.data.elapsed || 0
        }
      }
      break
    case 'done':
    case 'complete':
      {
        const m = s.messages[s.messages.length - 1]
        if (m) m.streaming = false
      }
      break
    case 'mode_change':
      planMode.value = event.data.mode === 'plan'
      emit('plan-mode-change', planMode.value)
      break
    case 'aborted':
      s._currentContent += '\n\n⚠ 已中断生成'
      break
    case 'error':
      s._currentContent += `\n\n⚠ 错误: ${event.data.error || '未知错误'}`
      break
  }
}

async function doExport() {
  try {
    await exportSession(activeSessionId.value, props.workspace || undefined)
  } catch (e: any) {
    alert(e.message || '导出失败')
  }
}

async function sendMessage(text: string) {
  if (!text.trim() || isStreaming.value) return

  if (text === '/plan') {
    planMode.value = true
    sendPlan(activeSessionId.value)
    emit('plan-mode-change', true)
    return
  }
  if (text === '/act') {
    planMode.value = false
    sendAct(activeSessionId.value)
    emit('plan-mode-change', false)
    return
  }
  if (text.startsWith('/clear')) {
    await resetChat(activeSessionId.value, props.workspace)
    const s = _state(activeSessionId.value)
    s.messages = []
    messages.value = []
    await fetchConfig()
    return
  }

  const sid = activeSessionId.value
  _save()
  const s = _state(sid)
  s._currentContent = ''
  s._currentThinking = ''
  s.isStreaming = true
  isStreaming.value = true
  s.messages = [...s.messages, { role: 'user', content: text, timestamp: _localTs() }, { role: 'assistant', content: '', tools: [], streaming: true, timestamp: '' }]
  messages.value = [...s.messages]

  emit('status-change', sid, 'generating')

  _isNearBottom = true
  await nextTick()
  scrollToBottom()

  const userMsgCount = s.messages.filter(m => m.role === 'user').length
  if (userMsgCount === 1) {
    const firstMsg = s.messages.find(m => m.role === 'user')
    if (firstMsg) renameSession(sid, firstMsg.content.slice(0, 20), props.workspace).catch(() => {})
  }

  let wsOk = false
  try { wsOk = await ensureWs() } catch { wsOk = false }
  if (wsOk) {
    wsChat(text, sid, props.workspace, planMode.value)
  } else {
    const s2 = _state(sid)
    const msg2 = s2.messages[s2.messages.length - 1]
    if (msg2) { msg2.streaming = false; msg2.content = '⚠ WebSocket 连接失败，请刷新页面重试' }
    s2.isStreaming = false
    isStreaming.value = false
    messages.value = [...s2.messages]
  }
}

function stopGeneration() {
  abortChat(activeSessionId.value, props.workspace)
  isStreaming.value = false
  const key = _cacheKey(activeSessionId.value, props.workspace)
  const s = _states.get(key)
  if (s) {
    s.isStreaming = false
    const last = s.messages[s.messages.length - 1]
    if (last && last.streaming) {
      s.messages[s.messages.length - 1] = { ...last, streaming: false, content: s._currentContent + '\n\n⚠ 已中断生成' }
      messages.value = [...s.messages]
    }
  }
}

function useSkill(name: string) {
  sendMessage(`/skill ${name}`)
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    _isNearBottom = true
  }
}

function _onChatScroll() {
  const el = chatContainer.value
  if (!el) return
  _isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD
}

async function switchToSession(sid: string, ws?: string) {
  _save()
  activeSessionId.value = sid
  localStorage.setItem(SESSION_KEY, sid)
  const s = _state(sid)
  if (s.messages.length === 0) {
    await restoreHistory(sid, ws || props.workspace || 'default')
  } else {
    _load(sid)
  }
  await fetchConfig()
}

defineExpose({ useSkill, switchToSession, activeSessionId, planMode })
</script>

<template>
  <div class="chat-container">
  <div class="chat-toolbar" v-if="messages.length > 0">
    <button class="export-btn" @click="doExport" title="导出 Markdown">📥 导出</button>
  </div>
  <div class="chat-view" ref="chatContainer" @scroll="_onChatScroll">
    <div class="messages" v-if="messages.length === 0">
      <div class="empty-state">
        <div class="empty-icon">m</div>
        <p class="empty-title">mini_ai</p>
        <p class="empty-sub">开始一段对话</p>
      </div>
    </div>
    <div class="messages">
      <MessageItem
        v-for="(msg, i) in messages"
        :key="i"
        :message="msg"
        :style="{ animationDelay: `${i * 0.05}s` }"
        class="fade-in-up"
      />
    </div>
  </div>
  <div class="input-area">
    <InputBar :disabled="isStreaming" :is-streaming="isStreaming" @send="sendMessage" @stop="stopGeneration" />
  </div>
  </div>
</template>

<style scoped>






.chat-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.chat-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 0.3rem 0.8rem;
  border-bottom: 0.5px solid var(--border-light);
  background: var(--bg);
}

.export-btn {
  padding: 0.25rem 0.6rem;
  border: 0.5px solid var(--border);
  border-radius: 5px;
  background: var(--bg-card);
  color: var(--fg-dim);
  font-size: 0.75rem;
  cursor: pointer;
  transition: all 0.15s ease;
}
.export-btn:hover { color: var(--fg); background: var(--bg-thinking); }

.chat-view {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0;
}

.messages {
  padding: 0 3%;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 6rem 0;
  opacity: 0.6;
}

.empty-icon {
  width: 64px;
  height: 72px;
  border-radius: 16px;
  background: var(--accent);
  color: var(--bg);
  font-family: 'Playfair Display', serif;
  font-weight: 700;
  font-size: 2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1.5rem;
}

.empty-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 0.3rem;
}

.empty-sub {
  color: var(--fg-muted);
  font-size: 0.95rem;
}

.input-area {
  flex-shrink: 0;
  border-top: 0.5px solid var(--border);
  background: var(--bg);
}




</style>
