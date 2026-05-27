<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed, watch } from 'vue'
import {
  ensureWs, onWsEvent, wsChat, abortChat, closeWs, sendPlan, sendAct,
  getConfig, createSession, getHistory, resetChat, renameSession,
  getSessions, getWorkspaces,
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
const _states = new Map<string, SessionState>()
const activeSessionId = ref('')
const messages = ref<Message[]>([])
const isStreaming = ref(false)
const planMode = ref(false)
const chatContainer = ref<HTMLElement>()
const props = defineProps<{ workspace?: string }>()
const emit = defineEmits(['config-update', 'status-change', 'plan-mode-change'])

let _unsubWs: (() => void) | null = null

function _state(sid: string): SessionState {
  if (!_states.has(sid)) {
    _states.set(sid, { messages: [], isStreaming: false, _currentContent: '', _currentThinking: '' })
  }
  return _states.get(sid)!
}

function _save() {
  const s = _states.get(activeSessionId.value)
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
})

async function initSession(ws?: string) {
  const stored = localStorage.getItem(SESSION_KEY)
  if (stored) {
    activeSessionId.value = stored
    await restoreHistory(stored, ws || props.workspace || 'default')
  } else {
    await newSession()
  }
  preloadAllSessions(ws || props.workspace || 'default')
}

async function preloadAllSessions(ws?: string) {
  try {
    const resp = await getSessions(ws || undefined)
    const sessions = resp.sessions || []
    for (const s of sessions) {
      if (s.session_id === activeSessionId.value) continue
      if (_states.has(s.session_id) && _states.get(s.session_id)!.messages.length > 0) continue
      restoreHistory(s.session_id, ws).catch(() => {})
    }
    const resp2 = await getWorkspaces()
    for (const w of resp2.workspaces || []) {
      if (w.name === ws) continue
      const resp3 = await getSessions(w.name)
      for (const s of resp3.sessions || []) {
        if (_states.has(s.session_id) && _states.get(s.session_id)!.messages.length > 0) continue
        restoreHistory(s.session_id, w.name).catch(() => {})
      }
    }
  } catch {}
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
  try {
    const resp = await getHistory(sid, ws || props.workspace)
    const raw = (resp.history || []).filter((m: any) => m.role !== 'system' && m.role !== 'tool')
      const merged: Message[] = []
    for (const m of raw) {
      if (m.role === 'assistant' && merged.length > 0 && merged[merged.length - 1].role === 'assistant') {
        const prev = merged[merged.length - 1]
        if (m.tool_calls) {
          const tools = m.tool_calls.map((tc: any) => ({
            name: tc.function?.name || '?',
            args: tc.function?.arguments || '',
            result: tc._result || '',
            elapsed: 0,
          }))
          prev.tools = [...(prev.tools || []), ...tools]
        }
        if (m.content) prev.content = (prev.content || '') + m.content
        if (m.thinking && typeof m.thinking === 'object') prev.thinking = m.thinking
        else if (m.thinking && typeof m.thinking === 'string') prev.thinking = { chars: m.thinking.length, elapsed: 0, content: m.thinking }
      } else {
        const msg: Message = { role: m.role as 'user' | 'assistant', content: m.content || '', timestamp: m.timestamp || '' }
        if (m.thinking) {
          if (typeof m.thinking === 'object') msg.thinking = m.thinking
          else if (typeof m.thinking === 'string') msg.thinking = { chars: m.thinking.length, elapsed: 0, content: m.thinking }
        }
        if (m.tool_calls && m.role === 'assistant') {
          msg.tools = m.tool_calls.map((tc: any) => ({
            name: tc.function?.name || '?',
            args: tc.function?.arguments || '',
            result: tc._result || '',
            elapsed: 0,
          }))
        }
        merged.push(msg)
      }
    }
    const s = _state(sid)
    s.messages = merged
    console.log('[restoreHistory DONE]', sid, 'ws=', ws, 'merged=', merged.length, 'active=', activeSessionId.value, 'content_lens=', merged.map((m:any) => (m.content||'').length))
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
    const c = await getConfig(activeSessionId.value)
    emit('config-update', c)
  } catch {}
}

function handleWsEvent(event: WsEvent) {
  const sid = event.data?.session_id || activeSessionId.value
  const s = _state(sid)

  _processEvent(s, event)

  if (event.event === 'done' || event.event === 'aborted' || event.event === 'error') {
    s.isStreaming = false
    s._currentContent = ''
    s._currentThinking = ''
    emit('status-change', sid, 'idle')
  }

  if (sid === activeSessionId.value) {
    // Force Vue reactivity: replace last message with a fresh object copy
    const lastIdx = s.messages.length - 1
    if (lastIdx >= 0) s.messages[lastIdx] = { ...s.messages[lastIdx] }
    messages.value = [...s.messages]
    isStreaming.value = s.isStreaming
    if (event.event === 'done' || event.event === 'aborted' || event.event === 'error') {
      fetchConfig()
    }
    nextTick(() => scrollToBottom())
  }
}

function _processEvent(s: SessionState, event: WsEvent) {
  const msg = s.messages[s.messages.length - 1]

  switch (event.event) {
    case 'thinking_start':
      s._currentThinking = ''
      break
    case 'thinking':
      s._currentThinking += event.data.content || ''
      break
    case 'thinking_end':
      if (msg && msg.role === 'assistant') {
        msg.thinking = {
          chars: event.data.chars || s._currentThinking.length,
          elapsed: event.data.elapsed || 0,
          content: s._currentThinking,
        }
      }
      s._currentThinking = ''
      break
    case 'text':
      s._currentContent += event.data.content || ''
      if (msg && msg.role === 'assistant') {
        msg.content = s._currentContent
            } else {
            }
      break
    case 'tool_start':
      if (msg && msg.role === 'assistant') {
        if (!msg.tools) msg.tools = []
        msg.tools.push({ name: event.data.name || '?', args: event.data.args || '', result: '...', elapsed: 0 })
      }
      break
    case 'tool_result':
      if (msg && msg.role === 'assistant' && msg.tools && msg.tools.length > 0) {
        const last = msg.tools[msg.tools.length - 1]
        last.result = event.data.result || ''
        last.elapsed = event.data.elapsed || 0
      }
      break
    case 'done':
      if (msg) msg.streaming = false
      break
    case 'mode_change':
      planMode.value = event.data.mode === 'plan'
      emit('plan-mode-change', planMode.value)
      break
    case 'aborted':
      if (msg) { msg.streaming = false; msg.content += '\n\n⚠ 已中断生成' }
      break
    case 'error':
      if (msg) { msg.streaming = false; msg.content += `\n\n⚠ 错误: ${event.data.error || '未知错误'}` }
      break
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
    await resetChat(activeSessionId.value)
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
  s.messages = [...s.messages, { role: 'user', content: text, timestamp: new Date().toISOString().slice(0,19) }, { role: 'assistant', content: '', tools: [], streaming: true, timestamp: '' }]
  messages.value = [...s.messages]

  emit('status-change', sid, 'generating')

  await nextTick()
  scrollToBottom()

  const userMsgCount = s.messages.filter(m => m.role === 'user').length
  if (userMsgCount === 1) {
    const firstMsg = s.messages.find(m => m.role === 'user')
    if (firstMsg) renameSession(sid, firstMsg.content.slice(0, 20)).catch(() => {})
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
  abortChat(activeSessionId.value)
}

function useSkill(name: string) {
  sendMessage(`/skill ${name}`)
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

async function switchToSession(sid: string, ws?: string) {
  const s0 = _state(sid)
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
  <div class="chat-view" ref="chatContainer">
    <div class="messages" v-if="messages.length === 0">
      <div class="empty-state">
        <div class="empty-icon">m</div>
        <p class="empty-title">mini_ai</p>
        <p class="empty-sub">开始一段对话</p>
      </div>
    </div>
    <div class="messages" v-else>
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
    <button v-if="isStreaming" class="stop-btn" @click="stopGeneration" title="停止生成">
      ⏹ 停止
    </button>
    <InputBar :disabled="isStreaming" @send="sendMessage" />
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

.chat-view {
  flex: 1;
  overflow-y: auto;
  padding: 2rem 0;
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

.stop-btn {
  display: block;
  margin: 0.5rem auto 0;
  padding: 0.3rem 1rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
  color: var(--fg-muted);
  font-size: 0.82rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.stop-btn:hover {
  border-color: #e55;
  color: #e55;
  background: var(--bg-thinking);
}
</style>
