<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed, watch } from 'vue'
import {
  ensureWs, onWsEvent, wsChat, abortChat, closeWs, sendPlan, sendAct,
  getConfig, createSession, getHistory, resetChat, renameSession,
  getSessions, getWorkspaces, exportSession,
  getSystemPrompt,
  type WsEvent, type HistoryMessage,
} from '../api'
import MessageItem from './MessageItem.vue'
import InputBar from './InputBar.vue'

interface Message {
  role: 'user' | 'assistant'
  content: string

  thinking?: { chars: number; elapsed: number; content: string }
  tools?: { name: string; args: string; result: string; elapsed: number; tool_call_id?: string }[]
  streaming?: boolean
  timestamp?: string
  teammate?: string
  teammateColor?: string
}

interface SessionState {
  messages: Message[]
  isStreaming: boolean
  _currentContent: string
  _currentThinking: string
  draftText: string
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
const draftText = ref('')
const teammateColorMap: Record<string, string> = {
  researcher: '#4a9eff',
  coder: '#e8922d',
  reviewer: '#9b59b6',
  tester: '#27ae60',
  planner: '#e67e22',
}

function _tmLabel(tm: string): string {
  if (tm.startsWith('sub:')) return `📦 ${tm.slice(4)}`
  if (tm.startsWith('wf:')) return `🔀 ${tm.slice(3)}`
  return `🤖 ${tm}`
}

function _tmColor(name: string): string {
  const base = name.replace(/^(sub:|wf:)/, '')
  return teammateColorMap[base] || '#888'
}

const chatContainer = ref<HTMLElement>()
const props = defineProps<{ workspace?: string }>()
const emit = defineEmits(['config-update', 'status-change', 'plan-mode-change', 'todos-update'])

let _unsubWs: (() => void) | null = null
let _flushTimer: number | null = null
let _flushSid = ''
let _scrollTimer: number | null = null
let _isNearBottom = true
let _streamingWatchdog: number | null = null
let _lastWsEventTime = 0
const FLUSH_INTERVAL = 50
const SCROLL_INTERVAL = 100
const BOTTOM_THRESHOLD = 80
const STREAMING_TIMEOUT = 120_000  // 2 min — 若 isStreaming 期间无任何 WS 事件则兜底重置

function _state(sid: string): SessionState {
  const key = _cacheKey(sid, props.workspace)
  if (!_states.has(key)) {
    _states.set(key, { messages: [], isStreaming: false, _currentContent: '', _currentThinking: '', draftText: '' })
  }
  return _states.get(key)!
}

function _save() {
  // 先同步 UI 到 state，再 flush pending 流式内容（顺序重要：flush 修改的是 state）
  const key = _cacheKey(activeSessionId.value, props.workspace)
  let s = _states.get(key)
  if (!s) {
    s = _state(activeSessionId.value)
  }
  s.messages = [...messages.value]
  s.isStreaming = isStreaming.value
  s.draftText = draftText.value
  _flushState(activeSessionId.value)
}

function _load(sid: string) {
  const s = _state(sid)
  if (sid === activeSessionId.value) {
    messages.value = s.messages.map(m => ({ ...m }))
    isStreaming.value = s.isStreaming
    draftText.value = s.draftText
  }
}

function _scheduleFlush(sid: string) {
  _flushSid = sid
  if (_flushTimer !== null) return
  _flushTimer = window.setTimeout(() => {
    _flushTimer = null
    _doFlush()
  }, FLUSH_INTERVAL)
}

function _flushState(sid: string) {
  const key = _cacheKey(sid, props.workspace)
  const s = _states.get(key)
  if (!s) return
  // 只在 _currentContent 非空时同步（避免空字符串覆盖已完成的消息）
  if (s._currentContent) {
    const lastIdx = s.messages.length - 1
    if (lastIdx >= 0) {
      s.messages[lastIdx] = { ...s.messages[lastIdx], content: s._currentContent }
    }
  }
}

function _doFlush(sid?: string) {
  const targetSid = sid || _flushSid || activeSessionId.value
  _flushSid = ''  // 用完即清
  _flushState(targetSid)
  // 仅活跃会话刷新 UI
  if (targetSid === activeSessionId.value) {
    const key = _cacheKey(targetSid, props.workspace)
    const s = _states.get(key)
    if (!s) return
    messages.value = [...s.messages]
    isStreaming.value = s.isStreaming
  }
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

watch(() => props.workspace, async (ws) => {
  const effectiveWs = ws || 'default'
  if (!_initialized) {
    _initialized = true
    await initSession(effectiveWs).catch(() => {})
    if (activeSessionId.value) {
      fetchConfig().catch(() => {})
    }
  }
})

onUnmounted(() => {
  if (_unsubWs) _unsubWs()
  closeWs()
  if (_flushTimer !== null) { clearTimeout(_flushTimer); _flushTimer = null }
  if (_scrollTimer !== null) { clearTimeout(_scrollTimer); _scrollTimer = null }
  if (_streamingWatchdog !== null) { clearTimeout(_streamingWatchdog); _streamingWatchdog = null }
})

async function initSession(ws?: string) {
  console.time('[perf] initSession')
  const effectiveWs = ws || props.workspace || 'default'
  const stored = localStorage.getItem(SESSION_KEY)
  if (stored) {
    activeSessionId.value = stored
    await restoreHistory(stored, effectiveWs)
  } else if (effectiveWs === 'default') {
    await newSession(effectiveWs)
  } else {
    // 非 default 工作空间不自动创建会话，等用户手动新建
    activeSessionId.value = ''
    _load('')
  }
  preloadAllSessions(effectiveWs)
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
    await fetchConfig()
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


function _resetStreaming(sid: string, reason: string) {
  const key = _cacheKey(sid, props.workspace)
  const s = _states.get(key)
  if (s) s.isStreaming = false
  if (sid === activeSessionId.value) {
    isStreaming.value = false
  }
  console.warn(`[mini-ai] isStreaming reset: sid=${sid} reason=${reason}`)
  emit('status-change', sid, 'idle')
  const last = s?.messages[s.messages.length - 1]
  if (last && last.streaming) last.streaming = false
  if (s && sid === activeSessionId.value) messages.value = [...s.messages]
  if (_streamingWatchdog !== null) { clearTimeout(_streamingWatchdog); _streamingWatchdog = null }
}

function _startStreamingWatchdog(sid: string) {
  if (_streamingWatchdog !== null) clearTimeout(_streamingWatchdog)
  _streamingWatchdog = window.setTimeout(() => {
    _streamingWatchdog = null
    if (isStreaming.value) {
      _resetStreaming(sid, 'watchdog-timeout')
    }
  }, STREAMING_TIMEOUT)
}

function handleWsEvent(event: WsEvent) {
  const sid = event.data?.session_id || activeSessionId.value
  const s = _state(sid)

  _processEvent(s, event)
  _lastWsEventTime = Date.now()

  const isTerminal = event.event === 'done' || event.event === 'aborted' || event.event === 'error'

  if (isTerminal) {
    s.isStreaming = false
    if (sid === activeSessionId.value) isStreaming.value = false
  }

  if (sid === activeSessionId.value) {
    if (isTerminal) {
      if (_flushTimer !== null) { clearTimeout(_flushTimer); _flushTimer = null }
      _doFlush(sid)
      console.log(`[mini-ai] terminal event: sid=${sid} event=${event.event}`)
      if (event.data?.prompt_tokens !== undefined) {
        emit('config-update', {
          prompt_tokens: event.data.prompt_tokens,
          completion_tokens: event.data.completion_tokens || 0,
        })
      }
      fetchConfig()
      if (_streamingWatchdog !== null) { clearTimeout(_streamingWatchdog); _streamingWatchdog = null }
    } else {
      _scheduleFlush(sid)
      if (isStreaming.value) _startStreamingWatchdog(sid)
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

function _updateUI(s: SessionState) {
  const key = _cacheKey(activeSessionId.value, props.workspace)
  const activeS = _states.get(key)
  if (s === activeS) messages.value = [...s.messages]
}

function _processEvent(s: SessionState, event: WsEvent) {
  const msg = s.messages[s.messages.length - 1]

  switch (event.event) {
    case 'thinking_start':
      {
        const tm = event.data.teammate || ''
        if (tm) {
          const tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (tmMsg) tmMsg.thinking = { chars: 0, elapsed: 0, content: '' }
        } else {
          s._currentThinking = ''
        }
      }
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
        const tm = event.data.teammate || ''
        if (tm) {
          const tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (tmMsg && tmMsg.thinking) {
            tmMsg.thinking.chars = event.data.chars || tmMsg.thinking.chars
            tmMsg.thinking.elapsed = event.data.elapsed || 0
          }
        } else {
          const m = s.messages[s.messages.length - 1]
          if (m && m.role === 'assistant' && m.thinking) {
            m.thinking.chars = event.data.chars || m.thinking.chars
            m.thinking.elapsed = event.data.elapsed || 0
          }
        }
      }
      break
    case 'text':
      {
        const tm = event.data.teammate || ''
        if (tm) {
          const tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (tmMsg) {
            tmMsg.content = (tmMsg.content || '') + (event.data.content || '')
            _updateUI(s)
          }
        } else {
          s._currentContent += event.data.content || ''
        }
      }
      break
    case 'tool_start':
      {
        const tm = event.data.teammate || ''
        if (tm) {
          let tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (!tmMsg) {
            tmMsg = { role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs(), teammate: tm, teammateColor: _tmColor(tm) }
            s.messages.push(tmMsg)
          }
          if (!tmMsg.tools) tmMsg.tools = []
          tmMsg.tools.push({ name: event.data.name || '?', args: event.data.args || '', result: '...', elapsed: 0, tool_call_id: event.data.tool_call_id || '' })
          _updateUI(s)
        } else {
          const m = s.messages[s.messages.length - 1]
          if (m && m.role === 'assistant') {
            if (!m.tools) m.tools = []
            m.tools.push({ name: event.data.name || '?', args: event.data.args || '', result: '...', elapsed: 0, tool_call_id: event.data.tool_call_id || '' })
            _updateUI(s)
          }
        }
      }
      break
    case 'todos':
      todosContent.value = event.data.content || ''
      emit('todos-update', todosContent.value)
      break
    case 'tool_result':
      {
        const tm = event.data.teammate || ''
        if (tm) {
          const tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (tmMsg && tmMsg.tools) {
            const tcId = event.data.tool_call_id || ''
            let target: any = null
            if (tcId) target = tmMsg.tools.find((t: any) => t.tool_call_id === tcId)
            if (!target) target = tmMsg.tools.find((t: any) => t.result === '...')
            if (!target) target = tmMsg.tools[tmMsg.tools.length - 1]
            target.result = event.data.result || ''
            target.elapsed = event.data.elapsed || 0
          }
          _updateUI(s)
          break
        }
        const tcId = event.data.tool_call_id || ''
        const m = s.messages[s.messages.length - 1]
        if (m && m.role === 'assistant' && m.tools && m.tools.length > 0) {
          let target: any = null
          if (tcId) target = m.tools.find((t: any) => t.tool_call_id === tcId)
          if (!target) target = m.tools.find((t: any) => t.result === '...')
          if (!target) target = m.tools[m.tools.length - 1]
          target.result = event.data.result || ''
          target.elapsed = event.data.elapsed || 0
          _updateUI(s)
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
    case 'teammate_status':
      if (event.data) {
        const tmName = event.data.name || ''
        const tmStatus = event.data.status || ''
        if (tmName && (tmStatus === 'idle' || tmStatus === 'shutdown' || tmStatus === 'offline')) {
          const tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tmName && m.streaming)
          if (tmMsg) {
            tmMsg.streaming = false
            _updateUI(s)
          }
        }
      }
      window.dispatchEvent(new CustomEvent('ws-message', { detail: event }))
      break
    case 'blackboard_update':
      window.dispatchEvent(new CustomEvent('ws-message', { detail: event }))
      break
    case 'error':
      s._currentContent += `\n\n⚠ 错误: ${event.data.error || '未知错误'}`
      break
  }
}

const showExportDialog = ref(false)
const exportLimit = ref(0)
const exportThinking = ref(false)
const exportTools = ref(false)

async function doExport() {
  exportLimit.value = 0
  exportThinking.value = false
  exportTools.value = false
  showExportDialog.value = true
}

async function confirmExport() {
  showExportDialog.value = false
  const limit = isNaN(exportLimit.value) ? 0 : (exportLimit.value || 0)
  try {
    await exportSession(activeSessionId.value, props.workspace || undefined, limit, exportThinking.value, exportTools.value)
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
  if (text === '/prompt') {
    try {
      const resp = await getSystemPrompt(props.workspace || undefined)
      const s = _state(activeSessionId.value)
      const promptContent = '📋 系统提示词（' + resp.length + ' 字符）：\n\n' + resp.system_prompt
      s.messages = [...s.messages, { role: 'assistant', content: promptContent, timestamp: _localTs() }]
      _updateUI(s)
    } catch (e: any) {
      console.error('getSystemPrompt failed', e)
    }
    return
  }

  const sid = activeSessionId.value
  draftText.value = ''  // 发送后清空草稿，再 save 确保落盘的是空值
  _save()
  const s = _state(sid)
  s._currentContent = ''
  s._currentThinking = ''
  s.isStreaming = true
  isStreaming.value = true
  _startStreamingWatchdog(sid)
  console.log(`[mini-ai] sendMessage: sid=${sid} isStreaming=true`)
  s.messages = [...s.messages, { role: 'user', content: text, timestamp: _localTs() }, { role: 'assistant', content: '', tools: [], streaming: true, timestamp: '' }]
  _updateUI(s)

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
  console.log(`[mini-ai] stopGeneration: sid=${activeSessionId.value}`)
  if (_streamingWatchdog !== null) { clearTimeout(_streamingWatchdog); _streamingWatchdog = null }
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
  _flushState(sid)  // 同步目标会话的 pending 流式内容
  if (s.messages.length === 0) {
    await restoreHistory(sid, ws || props.workspace || 'default')
  } else {
    _load(sid)
  }
  // 如果切回的会话仍在 streaming，启动 watchdog 兜底
  const loadedS = _state(sid)
  if (loadedS.isStreaming) {
    _startStreamingWatchdog(sid)
    console.log(`[mini-ai] switchToSession: sid=${sid} isStreaming=true, watchdog started`)
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
  <Teleport to="body">
    <div v-if="showExportDialog" class="export-overlay" @click.self="showExportDialog = false">
      <div class="export-dialog">
        <div class="export-title">导出会话</div>
        <div class="export-row">
          <label>消息条数</label>
          <input v-model.number="exportLimit" type="number" min="0" placeholder="0 = 全部" class="export-input" />
          <span class="export-hint">0 表示全部</span>
        </div>
        <div class="export-row"><label><input v-model="exportThinking" type="checkbox" /> 包含思考过程</label></div>
        <div class="export-row"><label><input v-model="exportTools" type="checkbox" /> 包含工具调用</label></div>
        <div class="export-actions">
          <button class="export-cancel" @click="showExportDialog = false">取消</button>
          <button class="export-confirm" @click="confirmExport">导出</button>
        </div>
      </div>
    </div>
  </Teleport>
  <div class="chat-view" ref="chatContainer" @scroll="_onChatScroll">
    <div class="messages" v-if="messages.length === 0">
      <div class="empty-state">
        <div class="empty-icon">m</div>
        <h2 class="empty-title">mini_ai<span class="empty-dot">.</span></h2>
        <p class="empty-sub">你的 AI 编程伙伴</p>
        <div class="empty-hints">
          <div class="empty-hint">
            <span class="hint-icon">⌨</span>
            <span>输入消息开始对话</span>
          </div>
          <div class="empty-hint">
            <span class="hint-icon">/</span>
            <span>输入 <code>/</code> 查看命令</span>
          </div>
          <div class="empty-hint">
            <span class="hint-icon">⇧</span>
            <span><code>Shift+Enter</code> 换行</span>
          </div>
        </div>
      </div>
    </div>
    <div class="messages">
      <MessageItem
        v-for="(msg, i) in messages"
        :key="i"
        :message="msg"
        :style="{ animationDelay: `${Math.min(i * 0.04, 0.6)}s` }"
        class="msg-anim"
      />

    </div>
  </div>
  <div class="input-area">
    <InputBar v-model="draftText" :disabled="isStreaming" :is-streaming="isStreaming" @send="sendMessage" @stop="stopGeneration" />
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

.export-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 200; display: flex; align-items: center; justify-content: center; animation: fadeIn 0.15s ease; }
.export-dialog { background: var(--bg); border: 0.5px solid var(--border); border-radius: 12px; padding: 1.2rem 1.5rem; width: 360px; box-shadow: 0 8px 32px var(--shadow); }
.export-title { font-size: 1rem; font-weight: 600; margin-bottom: 0.8rem; color: var(--fg); }
.export-row { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem; font-size: 0.85rem; color: var(--fg); }
.export-row label { display: flex; align-items: center; gap: 0.3rem; cursor: pointer; }
.export-input { width: 80px; padding: 0.25rem 0.5rem; border: 0.5px solid var(--border); border-radius: 5px; background: var(--bg-card); color: var(--fg); font-size: 0.85rem; }
.export-hint { font-size: 0.75rem; color: var(--fg-dim); }
.export-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.8rem; }
.export-cancel { padding: 0.35rem 0.8rem; border: 0.5px solid var(--border); border-radius: 5px; background: transparent; color: var(--fg-dim); font-size: 0.82rem; cursor: pointer; }
.export-cancel:hover { color: var(--fg); }
.export-confirm { padding: 0.35rem 0.8rem; border: none; border-radius: 5px; background: var(--accent); color: #fff; font-size: 0.82rem; cursor: pointer; }
.export-confirm:hover { background: var(--accent-hover); }

.chat-view {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0;
}

.msg-anim {
  animation: fadeInUp 0.35s ease both;
}

.messages {
  padding: 0 3%;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 5rem 0;
  animation: fadeInUp 0.5s ease;
}

.empty-icon {
  width: 72px;
  height: 80px;
  border-radius: 18px;
  background: var(--accent);
  color: var(--bg);
  font-family: 'Playfair Display', serif;
  font-weight: 700;
  font-size: 2.4rem;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1.2rem;
  box-shadow: 0 8px 32px rgba(232, 145, 45, 0.2);
}

.empty-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--fg);
  margin-bottom: 0;
}

.empty-dot {
  color: var(--accent);
}

.empty-sub {
  color: var(--fg-muted);
  font-size: 0.95rem;
  margin-bottom: 2.5rem;
  font-style: italic;
}

.empty-hints {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.empty-hint {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  color: var(--fg-muted);
  padding: 0.4rem 0.8rem;
  border-radius: 6px;
  background: var(--bg-card);
  transition: background 0.2s ease, color 0.2s ease;
}

.empty-hint:hover {
  background: var(--bg-thinking);
  color: var(--fg);
}

.hint-icon {
  font-size: 0.9rem;
  width: 24px;
  text-align: center;
  flex-shrink: 0;
}

.empty-hint :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.82rem;
  padding: 0.1em 0.4em;
  background: var(--bg-code);
  border-radius: 3px;
}



.input-area {
  flex-shrink: 0;
  border-top: 0.5px solid var(--border);
  background: var(--bg);
}




</style>
