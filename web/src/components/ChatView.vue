<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed, watch } from 'vue'
import {
  ensureWs, onWsEvent, wsChat, abortChat, closeWs, sendPlan, sendAct, isWsConnected,
  getConfig, createSession, getHistory, resetChat, renameSession,
  getSessions, getWorkspaces, exportSession,
  getSystemPrompt, getTodos, getTools,
  type WsEvent, type HistoryMessage, type ImageData,
} from '../api'
import MessageItem from './MessageItem.vue'
import InputBar, { type ImageFile } from './InputBar.vue'

interface Message {
  role: 'user' | 'assistant'
  content: string
  images?: ImageData[]  // 用户消息中的图片
  thinking?: { chars: number; elapsed: number; content: string }
  tools?: { name: string; args: string; result: string; elapsed: number; tool_call_id?: string }[]
  streaming?: boolean
  timestamp?: string
  teammate?: string
  teammateColor?: string
  workflow?: { status: string; tasks: Record<string, { status: string; agent: string; prompt?: string; result?: string }> }
}

interface SessionState {
  messages: Message[]
  isStreaming: boolean
  _currentContent: string
  _currentThinking: string
  draftText: string
  // 多 Agent 并行：每个队友独立的缓冲区
  _teammateBuffers: Map<string, { content: string; thinking: string }>
  // 会话级别工作流状态
  workflowState?: WorkflowState
  // 事件序号（用于检测消息丢失）
  _lastSeq: number
  // 事件去重
  _seenEvents: Set<string>
}

// 工作流状态定义
interface WorkflowState {
  status: 'idle' | 'running' | 'done' | 'failed'
  tasks: Record<string, {
    id: string
    agent: string
    status: 'pending' | 'running' | 'done' | 'failed'
    prompt?: string
    result?: string
    depends_on?: string[]
  }>
  elapsed?: number
  completed?: number
  failed?: number
  total?: number
}

const SESSION_KEY = 'mini-ai-session-id'

function _localTs(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return [d.getFullYear(), pad(d.getMonth()+1), pad(d.getDate())].join('-') + 'T' + [pad(d.getHours()), pad(d.getMinutes()), pad(d.getSeconds())].join(':')
}
function _cacheKey(sid: string, ws?: string | null): string {
  return `${ws || 'default'}:${sid}`
}
const _states = new Map<string, SessionState>()
const activeSessionId = ref('')
const messages = ref<Message[]>([])
const isStreaming = ref(false)
const planMode = ref(false)
const todosContent = ref('')
const draftText = ref('')
// 响应式的工作流状态（用于触发 Vue 更新）
const workflowStateRef = ref<WorkflowState | undefined>()
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
const showScrollBottom = ref(false)
const props = defineProps<{ workspace?: string }>()
const emit = defineEmits(['config-update', 'status-change', 'plan-mode-change', 'todos-update'])

let _unsubWs: (() => void) | null = null
let _flushTimer: number | null = null
let _flushSid = ''
let _scrollTimer: number | null = null
let _isNearBottom = true
let _streamingWatchdog: number | null = null
let _lastWsEventTime = 0
let _sessionDeleteHandler: ((e: Event) => void) | null = null  // 保存监听器引用
const FLUSH_INTERVAL = 50
const SCROLL_INTERVAL = 100
const BOTTOM_THRESHOLD = 80
const STREAMING_TIMEOUT = 120_000  // 2 min — 若 isStreaming 期间无任何 WS 事件则兜底重置

function _state(sid: string): SessionState {
  const key = _cacheKey(sid, props.workspace)
  if (!_states.has(key)) {
    _states.set(key, {
      messages: [],
      isStreaming: false,
      _currentContent: '',
      _currentThinking: '',
      draftText: '',
      _teammateBuffers: new Map(),
      workflowState: { status: 'idle', tasks: {} },
      _lastSeq: 0,
      _seenEvents: new Set()
    })
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
    
    // 检查是否真的在生成（验证 WebSocket 状态和超时）
    const isActiveGenerating = s.isStreaming && isWsConnected() && 
      _lastWsEventTime > 0 && (Date.now() - _lastWsEventTime < STREAMING_TIMEOUT)
    isStreaming.value = isActiveGenerating
    
    // 如果状态过期，清理流式标记
    if (s.isStreaming && !isActiveGenerating) {
      const last = s.messages[s.messages.length - 1]
      if (last && last.streaming) last.streaming = false
      s.isStreaming = false
      console.log(`[mini-ai] 会话 ${sid} 流式状态已过期，自动清理`)
      // 🔧 修复：通知 SessionSidebar 更新状态
      emit('status-change', sid, 'idle')
    }
    
    draftText.value = s.draftText
    // 同步工作流状态
    workflowStateRef.value = s.workflowState ? { ...s.workflowState, tasks: { ...s.workflowState.tasks } } : undefined
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
    
    // 验证生成状态（与 _load 保持一致）
    const isActiveGenerating = s.isStreaming && isWsConnected() && 
      _lastWsEventTime > 0 && (Date.now() - _lastWsEventTime < STREAMING_TIMEOUT)
    isStreaming.value = isActiveGenerating
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
  
  // 监听会话删除事件，清理本地状态
  _sessionDeleteHandler = (e: Event) => {
    const customEvent = e as CustomEvent<{ sid: string; ws: string | null }>
    const { sid, ws } = customEvent.detail
    const key = _cacheKey(sid, ws)
    
    if (_states.has(key)) {
      _states.delete(key)
      console.log(`[mini-ai] 已清理会话 ${sid} 的本地状态`)
    }
    
    // 如果删除的是当前会话，清空 UI
    if (sid === activeSessionId.value) {
      messages.value = []
      isStreaming.value = false
      draftText.value = ''
    }
  }
  window.addEventListener('session-delete', _sessionDeleteHandler as EventListener)
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
  
  // 移除事件监听（使用保存的引用）
  if (_sessionDeleteHandler) {
    window.removeEventListener('session-delete', _sessionDeleteHandler as EventListener)
    _sessionDeleteHandler = null
  }
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
  // 只预加载当前工作空间的会话历史，其他工作空间懒加载（避免 N×M 次 API 请求）
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
        const msg: Message = { role: m.role as 'user' | 'assistant', content: m.content || '', timestamp: m.timestamp || '' }
        if (m.images) msg.images = m.images
        merged.push(msg)
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

async function handleWsEvent(event: WsEvent) {
  // 处理连接状态事件
  if (event.event === 'connected') {
    console.log('[mini-ai] WebSocket 已连接')
    emit('status-change', activeSessionId.value, 'connected')
    return
  }
  
  if (event.event === 'disconnected') {
    console.warn('[mini-ai] WebSocket 已断开', event.data)
    emit('status-change', activeSessionId.value, 'disconnected')
    return
  }
  
  // 处理重连事件，重置所有会话的生成状态
  if (event.event === 'reconnected') {
    console.log('[mini-ai] WebSocket 已重连，重置所有会话状态')
    _states.forEach((s, key) => {
      s.isStreaming = false
      if (s.messages.length > 0) {
        const last = s.messages[s.messages.length - 1]
        if (last && last.streaming) last.streaming = false
      }
    })
    isStreaming.value = false
    emit('status-change', activeSessionId.value, 'idle')
    // 重连后主动刷新配置，确保上下文 token 数与后端一致
    fetchConfig().catch(() => {})
    return
  }
  
  const sid = event.data?.session_id || activeSessionId.value
  const s = _state(sid)

  // 检测事件序号缺口（消息丢失检测）
  const seq = event.data?.seq
  if (seq !== undefined) {
    const expectedSeq = s._lastSeq + 1
    if (seq > expectedSeq) {
      console.warn(`[mini-ai] 检测到事件丢失: expected=${expectedSeq}, got=${seq}, gap=${seq - expectedSeq}`)
    }
    s._lastSeq = seq
  }

  // 事件去重（使用 event_id 或 seq+event 组合）
  const eventId = event.data?.event_id || (seq !== undefined ? `${seq}:${event.event}` : null)
  if (eventId && s._seenEvents.has(eventId)) {
    console.log(`[mini-ai] 跳过重复事件: ${eventId}`)
    return
  }
  if (eventId) {
    s._seenEvents.add(eventId)
    // 限制去重集合大小，避免内存泄漏（简单清空，避免 Set 无序问题）
    if (s._seenEvents.size > 1000) {
      s._seenEvents.clear()
    }
  }

  _processEvent(s, event)
  _lastWsEventTime = Date.now()

  const isTerminal = event.event === 'done' || event.event === 'aborted' || event.event === 'error' || event.event === 'complete'

  // 🔧 简化：终端事件时，直接重置会话状态
  if (isTerminal) {
    s.isStreaming = false
    s._currentContent = ''
    s._currentThinking = ''
    
    // 如果是当前活跃会话，重置全局 isStreaming
    if (sid === activeSessionId.value) {
      isStreaming.value = false
      console.log(`[mini-ai] terminal event reset isStreaming: sid=${sid} event=${event.event}`)
    }
    
    emit('status-change', sid, 'idle')
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
  if (s === activeS) {
    messages.value = [...s.messages]
    // 同步工作流状态到响应式 ref
    workflowStateRef.value = s.workflowState ? { ...s.workflowState, tasks: { ...s.workflowState.tasks } } : undefined
  }
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
      {
        const tm = event.data.teammate || ''
        if (tm) {
          // 使用队友独立缓冲区
          if (!s._teammateBuffers.has(tm)) {
            s._teammateBuffers.set(tm, { content: '', thinking: '' })
          }
          const buf = s._teammateBuffers.get(tm)!
          buf.thinking += event.data.content || ''
          
          // 查找或创建队友消息
          let tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (!tmMsg) {
            tmMsg = { role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs(), teammate: tm, teammateColor: _tmColor(tm) }
            s.messages.push(tmMsg)
          }
          tmMsg.thinking = { chars: buf.thinking.length, elapsed: 0, content: buf.thinking }
          _updateUI(s)
        } else {
          s._currentThinking += event.data.content || ''
          const m = s.messages[s.messages.length - 1]
          if (m && m.role === 'assistant') {
            m.thinking = { chars: s._currentThinking.length, elapsed: 0, content: s._currentThinking }
          }
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
          // 使用队友独立缓冲区
          if (!s._teammateBuffers.has(tm)) {
            s._teammateBuffers.set(tm, { content: '', thinking: '' })
          }
          const buf = s._teammateBuffers.get(tm)!
          buf.content += event.data.content || ''
          
          // 查找或创建队友消息
          let tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (!tmMsg) {
            tmMsg = { role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs(), teammate: tm, teammateColor: _tmColor(tm) }
            s.messages.push(tmMsg)
          }
          tmMsg.content = buf.content
          _updateUI(s)
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
        const tcId = event.data.tool_call_id || ''
        
        if (tm) {
          const tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (tmMsg && tmMsg.tools) {
            let target: any = null
            // 优先按 tool_call_id 精确匹配
            if (tcId) target = tmMsg.tools.find((t: any) => t.tool_call_id === tcId)
            // 否则按 name + 占位符匹配（同名工具可能在同一轮调用多次）
            if (!target) {
              const name = event.data.name || ''
              target = tmMsg.tools.find((t: any) => t.name === name && t.result === '...')
            }
            // 最后兜底：找最后一个占位符
            if (!target) target = tmMsg.tools.find((t: any) => t.result === '...')
            if (!target) target = tmMsg.tools[tmMsg.tools.length - 1]
            
            if (target) {
              target.result = event.data.result || ''
              target.elapsed = event.data.elapsed || 0
              _updateUI(s)
            } else {
              console.warn('[tool_result] 未找到匹配的工具调用', { tm, tcId, name: event.data.name })
            }
          }
          break
        }
        
        // 主 Agent 的工具
        const m = s.messages[s.messages.length - 1]
        if (m && m.role === 'assistant' && m.tools && m.tools.length > 0) {
          let target: any = null
          // 优先按 tool_call_id 精确匹配
          if (tcId) target = m.tools.find((t: any) => t.tool_call_id === tcId)
          // 否则按 name + 占位符匹配
          if (!target) {
            const name = event.data.name || ''
            target = m.tools.find((t: any) => t.name === name && t.result === '...')
          }
          // 最后兜底：找最后一个占位符
          if (!target) target = m.tools.find((t: any) => t.result === '...')
          if (!target) target = m.tools[m.tools.length - 1]
          
          if (target) {
            target.result = event.data.result || ''
            target.elapsed = event.data.elapsed || 0
            _updateUI(s)
          } else {
            console.warn('[tool_result] 未找到匹配的工具调用', { tcId, name: event.data.name })
          }
        }
      }
      break
    case 'done':
    case 'complete':
      {
        const m = s.messages[s.messages.length - 1]
        if (m) s.messages[s.messages.length - 1] = { ...m, streaming: false }
        
        // 🔧 complete 事件携带错误：始终显示，追加到已有内容
        if (event.data?.error) {
          console.error('[complete] LLM 返回错误:', event.data.error)
          if (m && m.role === 'assistant') {
            s.messages[s.messages.length - 1] = { ...m, content: (m.content ? m.content + '\n\n' : '') + event.data.error, streaming: false }
            _updateUI(s)
          } else {
            // 无 assistant 消息：新增一条
            s.messages.push({
              role: 'assistant',
              content: event.data.error,
              timestamp: new Date().toISOString()
            })
            _updateUI(s)
          }
        }
        
        console.log('[mini-ai] cpl: msgs=' + s.messages.length + ' last=' + (s.messages[s.messages.length-1]?.role) + ':' + (s.messages[s.messages.length-1]?.content || '').substring(0, 60) + ' err=' + !!event.data?.error)
        _updateUI(s)
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
          // 清除队友缓冲区
          s._teammateBuffers.delete(tmName)
        }
      }
      window.dispatchEvent(new CustomEvent('ws-message', { detail: event }))
      break
    case 'blackboard_update':
      window.dispatchEvent(new CustomEvent('ws-message', { detail: event }))
      break
    case 'inbox_message':
      // 处理队友间的消息通知
      {
        const to = event.data?.to || 'lead'
        const from = event.data?.from || ''
        const count = event.data?.count || 0
        if (from && count > 0) {
          // 在消息列表中添加一条通知消息
          const lastMsg = s.messages[s.messages.length - 1]
          // 避免连续重复通知
          const lastInboxNotice = s.messages.slice(-3).find(m => 
            m.role === 'assistant' && m.content?.includes(`📧 ${from} → ${to}`)
          )
          if (!lastInboxNotice) {
            s.messages.push({
              role: 'assistant',
              content: `📧 **${from} → ${to}**: ${count} 条新消息`,
              timestamp: _localTs(),
              streaming: false
            })
            _updateUI(s)
          }
        }
      }
      break
    case 'info':
      // 显示系统提示信息（如压缩结果）
      s.messages.push({
        role: 'assistant',
        content: `ℹ️ ${event.data.message || '系统提示'}`,
        timestamp: _localTs(),
        streaming: false
      })
      _updateUI(s)
      break
    case 'error':
      {
        const errMsg = event.data.error || '未知错误'
        // 先将未刷新的流式内容同步到消息上，避免丢失已生成的部分文本
        if (s._currentContent) {
          const lastIdx = s.messages.length - 1
          if (lastIdx >= 0 && s.messages[lastIdx].role === 'assistant') {
            s.messages[lastIdx] = { ...s.messages[lastIdx], content: s._currentContent }
          }
        }
        const m = s.messages[s.messages.length - 1]
        if (m && m.role === 'assistant') {
          s.messages[s.messages.length - 1] = { ...m, content: m.content ? m.content + '\n\n' + errMsg : errMsg, streaming: false }
        } else {
          s.messages.push({ role: 'assistant', content: errMsg, timestamp: _localTs(), streaming: false })
        }
        console.log('[mini-ai] err: msgs=' + s.messages.length + ' last=' + (s.messages[s.messages.length-1]?.role) + ':' + (s.messages[s.messages.length-1]?.content || '').substring(0, 60))
        _updateUI(s)
      }
      break
    // ── 新增：工作流事件 ──
    case 'workflow_start':
      {
        const tasks = event.data.tasks || []
        const total = event.data.total || tasks.length

        // 更新会话级别工作流状态
        if (!s.workflowState) {
          s.workflowState = { status: 'running', tasks: {}, total }
        } else {
          s.workflowState.status = 'running'
          s.workflowState.total = total
        }
        tasks.forEach((t: any) => {
          s.workflowState!.tasks[t.id] = {
            id: t.id,
            agent: t.agent,
            status: 'pending',
            prompt: t.prompt,
            depends_on: t.depends_on || []
          }
        })
        console.log('[ChatView] workflow_start: workflowState updated:', s.workflowState)

        let content = `🔀 **工作流启动** (${total} 个任务)\n\n`
        tasks.forEach((t: any) => {
          const deps = t.depends_on && t.depends_on.length > 0 ? ` ← ${t.depends_on.join(', ')}` : ''
          content += `- **${t.id}** (${t.agent})${deps}\n`
        })
        s.messages.push({
          role: 'assistant',
          content,
          timestamp: _localTs(),
          streaming: false,
          workflow: { status: 'running', tasks: {} }
        })
        _updateUI(s)
        // 通知 WorkflowPanel
        window.dispatchEvent(new CustomEvent('workflow-event', { detail: { event: 'workflow_start', data: event.data } }))
      }
      break
    case 'task_start':
      {
        const taskId = event.data.id || ''
        const agent = event.data.agent || ''
        const prompt = event.data.prompt || ''

        // 更新会话级别工作流状态
        if (s.workflowState) {
          if (!s.workflowState.tasks[taskId]) {
            s.workflowState.tasks[taskId] = { id: taskId, agent, status: 'running', prompt }
          } else {
            s.workflowState.tasks[taskId].status = 'running'
          }
        }

        // 更新工作流消息
        const wfMsg = s.messages.slice().reverse().find(m => m.workflow)
        if (wfMsg && wfMsg.workflow) {
          wfMsg.workflow.tasks[taskId] = { status: 'running', agent }
          const runningCount = Object.values(wfMsg.workflow.tasks).filter((t: any) => t.status === 'running').length
          const doneCount = Object.values(wfMsg.workflow.tasks).filter((t: any) => t.status === 'done').length
          wfMsg.workflow.status = runningCount > 0 ? 'running' : 'idle'
          _updateUI(s)
        }
        // 添加任务开始通知
        s.messages.push({
          role: 'assistant',
          content: `▶ **${taskId}** (${agent}) 开始执行`,
          timestamp: _localTs(),
          streaming: false,
          teammate: `wf:${taskId}`,
          teammateColor: _tmColor(taskId)
        })
        _updateUI(s)
        window.dispatchEvent(new CustomEvent('workflow-event', { detail: { event: 'task_start', data: event.data } }))
      }
      break
    case 'task_end':
      {
        const taskId = event.data.id || ''
        const status = event.data.status || 'done'
        const result = event.data.result_preview || event.data.error || ''

        // 更新会话级别工作流状态
        if (s.workflowState && s.workflowState.tasks[taskId]) {
          s.workflowState.tasks[taskId].status = status === 'done' ? 'done' : 'failed'
          s.workflowState.tasks[taskId].result = result
        }

        // 更新工作流消息
        const wfMsg = s.messages.slice().reverse().find(m => m.workflow)
        if (wfMsg && wfMsg.workflow) {
          wfMsg.workflow.tasks[taskId] = {
            status,
            agent: wfMsg.workflow.tasks[taskId]?.agent || '',
            result: result.slice(0, 100)
          }
          const runningCount = Object.values(wfMsg.workflow.tasks).filter((t: any) => t.status === 'running').length
          const doneCount = Object.values(wfMsg.workflow.tasks).filter((t: any) => t.status === 'done').length
          const failedCount = Object.values(wfMsg.workflow.tasks).filter((t: any) => t.status === 'failed').length
          wfMsg.workflow.status = runningCount > 0 ? 'running' : (failedCount > 0 ? 'failed' : 'done')
          _updateUI(s)
        }
        // 添加任务完成通知
        const icon = status === 'done' ? '✅' : '❌'
        s.messages.push({
          role: 'assistant',
          content: `${icon} **${taskId}** ${status === 'done' ? '完成' : '失败'}${result ? `: ${result.slice(0, 100)}${result.length > 100 ? '...' : ''}` : ''}`,
          timestamp: _localTs(),
          streaming: false,
          teammate: `wf:${taskId}`,
          teammateColor: _tmColor(taskId)
        })
        _updateUI(s)
        window.dispatchEvent(new CustomEvent('workflow-event', { detail: { event: 'task_end', data: event.data } }))
      }
      break
    case 'workflow_end':
      {
        const elapsed = event.data.elapsed || 0
        const completed = event.data.completed || 0
        const failed = event.data.failed || 0
        const total = event.data.total || 0

        // 更新会话级别工作流状态
        if (s.workflowState) {
          s.workflowState.status = failed > 0 ? 'failed' : 'done'
          s.workflowState.elapsed = elapsed
          s.workflowState.completed = completed
          s.workflowState.failed = failed
        }

        // 更新工作流消息
        const wfMsg = s.messages.slice().reverse().find(m => m.workflow)
        if (wfMsg && wfMsg.workflow) {
          wfMsg.workflow.status = failed > 0 ? 'failed' : 'done'
          _updateUI(s)
        }
        // 添加工作流完成通知
        s.messages.push({
          role: 'assistant',
          content: `🏁 **工作流完成**\n\n- 耗时: ${elapsed}s\n- 成功: ${completed}/${total}\n${failed > 0 ? `- 失败: ${failed}` : ''}`,
          timestamp: _localTs(),
          streaming: false
        })
        _updateUI(s)
        window.dispatchEvent(new CustomEvent('workflow-event', { detail: { event: 'workflow_end', data: event.data } }))
      }
      break
    // ── 新增：Agent 启动事件 ──
    case 'agent_start':
      {
        const agentType = event.data.agent_type || ''
        const role = event.data.role || ''
        const task = event.data.task || ''
        const icon = agentType.startsWith('sub:') ? '📦' : '🤖'
        const label = agentType.startsWith('sub:') ? agentType.slice(4) : agentType
        s.messages.push({
          role: 'assistant',
          content: `${icon} **${label}** 已启动${role ? ` (${role})` : ''}\n\n任务: ${task}`,
          timestamp: _localTs(),
          streaming: false,
          teammate: agentType,
          teammateColor: _tmColor(agentType)
        })
        _updateUI(s)
      }
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

async function sendMessage(text: string, images?: ImageFile[]) {
  if (!text.trim() && (!images || images.length === 0)) return
  
  const sid = activeSessionId.value
  const s = _state(sid)
  
  // 检查该会话是否正在生成（避免多会话并行冲突）
  if (s.isStreaming) {
    console.warn(`[mini-ai] 会话 ${sid} 正在生成中，拒绝发送`)
    return
  }
  
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
    await resetChat(activeSessionId.value, props.workspace || undefined)
    s.messages = []
    messages.value = []
    await fetchConfig()
    return
  }
  if (text === '/prompt') {
    try {
      const resp = await getSystemPrompt(props.workspace || undefined)
      const promptContent = '📋 系统提示词（' + resp.chars + ' 字符, ~' + resp.tokens + ' tokens）：\n\n' + resp.system_prompt
      s.messages = [...s.messages, { role: 'assistant', content: promptContent, timestamp: _localTs() }]
      _updateUI(s)
    } catch (e: any) {
      console.error('getSystemPrompt failed', e)
    }
    return
  }
  if (text === '/tools') {
    try {
      const resp = await getTools()
      const toolNames = resp.tool_names.join(', ')
      const content = '🔧 工具定义（' + resp.count + ' 个工具, ' + resp.chars + ' 字符, ~' + resp.tokens + ' tokens）：\n\n工具列表：' + toolNames + '\n\n完整定义：\n\n```json\n' + JSON.stringify(resp.tools, null, 2) + '\n```'
      s.messages = [...s.messages, { role: 'assistant', content: content, timestamp: _localTs() }]
      _updateUI(s)
    } catch (e: any) {
      console.error('getTools failed', e)
    }
    return
  }

  draftText.value = ''  // 发送后清空草稿，再 save 确保落盘的是空值
  _save()
  s._currentContent = ''
  s._currentThinking = ''
  s.isStreaming = true
  isStreaming.value = true
  _startStreamingWatchdog(sid)
  console.log(`[mini-ai] sendMessage: sid=${sid} isStreaming=true`)
  
  // 构造用户消息（包含图片）
  const userMsg: Message = { role: 'user', content: text, timestamp: _localTs() }
  if (images && images.length > 0) {
    userMsg.images = images.map(img => ({
      dataUrl: img.dataUrl,
      name: img.name,
      size: img.size
    }))
  }
  
  s.messages = [...s.messages, userMsg, { role: 'assistant', content: '', tools: [], streaming: true, timestamp: '' }]
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
    wsChat(text, sid, props.workspace, planMode.value, userMsg.images)
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
    showScrollBottom.value = false
  }
}

function _onChatScroll() {
  const el = chatContainer.value
  if (!el) return
  _isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD
  showScrollBottom.value = !_isNearBottom
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
    // 🔧 修复：通知 SessionSidebar 更新状态为 generating
    emit('status-change', sid, 'generating')
  }
  await fetchConfig()
  
  // 获取当前会话的 todos
  try {
    const resp = await getTodos(sid, ws || props.workspace)
    if (resp.todos && resp.todos.length > 0) {
      // 将 todos 转换为显示格式
      const lines = resp.todos.map((t: any) => {
        const icon = t.status === 'completed' ? '[x]' : t.status === 'in_progress' ? '[~]' : '[ ]'
        if (t.status === 'in_progress') {
          return `${icon} **${t.id}. ${t.content}** ← 当前`
        }
        return `${icon} ${t.id}. ${t.content}`
      })
      todosContent.value = '📋TODO\n' + lines.join('\n')
      emit('todos-update', todosContent.value)
    } else {
      todosContent.value = ''
      emit('todos-update', '')
    }
  } catch (err) {
    console.warn('[ChatView] getTodos failed:', err)
  }
}

defineExpose({ useSkill, switchToSession, activeSessionId, planMode, getCurrentWorkflowState })

// 获取当前会话的工作流状态
function getCurrentWorkflowState(): WorkflowState | undefined {
  console.log('[ChatView] getCurrentWorkflowState called, state:', workflowStateRef.value)
  return workflowStateRef.value
}
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
    <button v-if="showScrollBottom" class="scroll-bottom-btn" @click="scrollToBottom" title="滚动到底部">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>
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
  position: relative;
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

.scroll-bottom-btn {
  position: fixed;
  bottom: 90px;
  left: 50%;
  transform: translateX(-50%);
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--accent);
  border: 2px solid var(--accent);
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(232, 145, 45, 0.4);
  transition: all 0.2s ease;
  z-index: 1000;
}

.scroll-bottom-btn:hover {
  background: var(--accent-hover);
  transform: translateX(-50%) scale(1.1);
}

.scroll-bottom-btn svg {
  width: 22px;
  height: 22px;
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
