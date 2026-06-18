<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed, watch } from 'vue'
import {
  ensureWs, onWsEvent, wsChat, abortChat, closeWs, isWsConnected,
  startPlan, sendPlanMessage, approvePlan, cancelPlan, applyPlanDecision,
  getConfig, createSession, getHistory, resetChat, renameSession,
  getSessions, exportSession,
  getSystemPrompt, getTodos, getTools,
  type WsEvent, type HistoryMessage, type ImageData, type WorkflowState, type WorkflowTaskStatus,
} from '../api'
import MessageItem from './MessageItem.vue'
import InputBar, { type ImageFile } from './InputBar.vue'
import PlanModeBanner from './PlanModeBanner.vue'
import PlanApprovalBar from './PlanApprovalBar.vue'
import PlanChoiceDialog from './PlanChoiceDialog.vue'
import { usePlanSession } from '../plan/usePlanSession'
import { hasUnresolvedPlanInteractions, isFinalPlan, nextPlanInteraction } from '../plan/interactions'
import type { PlanArtifact, PlanChoiceConfirmPayload, PlanChoiceMode, PlanChoiceOption, PlanDecision, PlanDecisionOpenPayload, PlanInteraction, PlanState } from '../plan/types'

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
  workflow?: WorkflowState
  main?: boolean
  kind?: 'chat' | 'plan_discussion' | 'plan_artifact' | 'plan_interaction' | 'system_notice'
  plan?: PlanArtifact
  planInteraction?: PlanInteraction
}

interface SessionState {
  messages: Message[]
  isStreaming: boolean
  _currentContent: string
  _currentThinking: string
  draftText: string
  planState: PlanState
  currentPlan: PlanArtifact | null
  // 多 Agent 并行：每个队友独立的缓冲区
  _teammateBuffers: Map<string, { content: string; thinking: string }>
  // 会话级别工作流状态
  workflowState?: WorkflowState
  // 事件序号（用于检测消息丢失）
  _lastSeq: number
  // 事件去重
  _seenEvents: Set<string>
  // 最近一次流式事件时间（按会话隔离）
  _lastWsEventTime: number
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
const plan = usePlanSession()
const todosContent = ref('')
const draftText = ref('')
const effectiveAwaitingApproval = computed(() => _effectiveAwaitingApproval())
const effectiveInputMode = computed(() => {
  if (plan.state.planState === 'executing') return 'executing'
  if (effectiveAwaitingApproval.value) return 'awaiting_approval'
  if (plan.isPlanning.value) return 'planning'
  return 'chat'
})
// 响应式的工作流状态（用于触发 Vue 更新）
const workflowStateRef = ref<WorkflowState | undefined>()
const planDialog = ref<{
  visible: boolean
  mode: PlanChoiceMode
  title: string
  subtitle?: string
  options: PlanChoiceOption[]
  allowMultiple: boolean
  selectedIds: string[]
  customValue: string
  decision?: PlanDecision
  stepId?: string
  stepTitle?: string
}>({ visible: false, mode: 'option', title: '', options: [], allowMultiple: false, selectedIds: [], customValue: '' })
const autoOpenedApprovalKeys = new Set<string>()
const suppressedApprovalKeys = new Set<string>()
const completedInteractionIds = new Set<string>()
const teammateColorMap: Record<string, string> = {
  researcher: '#4a9eff',
  coder: '#e8922d',
  reviewer: '#9b59b6',
  tester: '#27ae60',
  planner: '#e67e22',
}

function _tmLabel(tm: string): string {
  if (tm.startsWith('sub:')) return tm.slice(4)
  if (tm.startsWith('wf:')) return tm.slice(3)
  return tm
}

function _tmColor(name: string): string {
  const base = name.replace(/^(sub:|wf:)/, '')
  return teammateColorMap[base] || '#888'
}

function _looksLikePlanArtifact(raw: unknown): raw is PlanArtifact {
  return !!(raw && typeof raw === 'object' && ['goal', 'summary', 'steps', 'options'].some(k => k in raw))
}

function _stripTrailingPlanJson(text: string): string {
  const source = text || ''
  for (let start = source.lastIndexOf('{'); start >= 0; start = source.lastIndexOf('{', start - 1)) {
    const candidate = source.slice(start).trim()
    try {
      const raw = JSON.parse(candidate)
      if (_looksLikePlanArtifact(raw)) return source.slice(0, start).trim()
    } catch {}
  }
  return source.trim()
}

function _stripPlanArtifactBlocks(text: string): string {
  const stripped = (text || '')
    .replace(/```plan-artifact[\s\S]*?```/gi, '')
    .replace(/```json[\s\S]*?```/gi, '')
    .trim()
  if (stripped.startsWith('{') && stripped.endsWith('}')) {
    try {
      if (_looksLikePlanArtifact(JSON.parse(stripped))) return ''
    } catch {}
  }
  return _stripTrailingPlanJson(stripped)
}

function _removeDraftPlanMessages(s: SessionState, planId: string) {
  s.messages = s.messages.filter(m => !(m.plan?.plan_id === planId && !isFinalPlan(m.plan)))
}

function _syncPlanWizardMessages(s: SessionState, planArtifact: PlanArtifact) {
  _removeDraftPlanMessages(s, planArtifact.plan_id)
  const interaction = nextPlanInteraction(planArtifact)
  const final = isFinalPlan(planArtifact)

  if (interaction) {
    const existing = s.messages.findIndex(m => m.kind === 'plan_interaction' && m.planInteraction?.id === interaction.id)
    const card: Message = {
      role: 'assistant',
      content: '',
      kind: 'plan_interaction',
      planInteraction: { ...interaction, completed: completedInteractionIds.has(interaction.id) },
      timestamp: _localTs(),
      streaming: false,
    }
    if (existing >= 0) s.messages[existing] = card
    else s.messages.push(card)
  }

  if (final) {
    const existing = s.messages.findIndex(m => m.kind === 'plan_artifact' && m.plan?.plan_id === planArtifact.plan_id)
    const card: Message = { role: 'assistant', content: '', kind: 'plan_artifact', plan: planArtifact, timestamp: _localTs(), streaming: false }
    if (existing >= 0) s.messages[existing] = card
    else s.messages.push(card)
  }
}

function _effectiveAwaitingApproval(): boolean {
  return !!(plan.awaitingApproval.value && plan.state.currentPlan && !hasUnresolvedPlanInteractions(plan.state.currentPlan))
}

const chatContainer = ref<HTMLElement>()
const showScrollBottom = ref(false)
const props = defineProps<{ workspace?: string }>()
const emit = defineEmits(['config-update', 'status-change', 'plan-mode-change', 'plan-update', 'todos-update'])

let _unsubWs: (() => void) | null = null
let _flushTimer: number | null = null
let _flushSid = ''
let _scrollTimer: number | null = null
let _isNearBottom = true
let _streamingWatchdog: number | null = null
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
      planState: 'idle',
      currentPlan: null,
      _teammateBuffers: new Map(),
      workflowState: { status: 'idle', tasks: {} },
      _lastSeq: 0,
      _seenEvents: new Set(),
      _lastWsEventTime: 0
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
      s._lastWsEventTime > 0 && (Date.now() - s._lastWsEventTime < STREAMING_TIMEOUT)
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
    plan.setState(s.planState, s.currentPlan)
    planMode.value = plan.isPlanning.value || s.planState === 'executing'
    emit('plan-mode-change', s.planState)
    emit('plan-update', s.currentPlan)
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

function _mainAssistantIndex(s: SessionState): number {
  for (let i = s.messages.length - 1; i >= 0; i--) {
    const m = s.messages[i]
    if (m.role === 'assistant' && m.main) return i
  }
  return -1
}

function _syncMainContent(s: SessionState) {
  if (!s._currentContent) return
  const idx = _mainAssistantIndex(s)
  if (idx >= 0) {
    const nextContent = s.messages[idx].kind === 'plan_discussion'
      ? _stripPlanArtifactBlocks(s._currentContent)
      : s._currentContent
    s.messages[idx] = { ...s.messages[idx], content: nextContent }
  }
}

function _flushState(sid: string) {
  const key = _cacheKey(sid, props.workspace)
  const s = _states.get(key)
  if (!s) return
  // 只在 _currentContent 非空时同步（避免空字符串覆盖已完成的消息）
  _syncMainContent(s)
}

function _doFlush(sid?: string) {
  const targetSid = sid || _flushSid || activeSessionId.value
  _flushSid = ''
  _flushState(targetSid)
  if (targetSid === activeSessionId.value) {
    const key = _cacheKey(targetSid, props.workspace)
    const s = _states.get(key)
    if (!s) return
    // Sync _currentContent into the main assistant message before any UI update
    _syncMainContent(s)
    messages.value = [...s.messages]
    const isActiveGenerating = s.isStreaming && isWsConnected() && 
      s._lastWsEventTime > 0 && (Date.now() - s._lastWsEventTime < STREAMING_TIMEOUT)
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
    const raw = resp.history || []  // 后端已过滤 system/tool，无需前端再过滤
    const restoredPlan = (resp.current_plan || null) as PlanArtifact | null
    if (sid === activeSessionId.value) {
      plan.restore(restoredPlan)
      planMode.value = plan.isPlanning.value
      emit('plan-mode-change', plan.state.planState)
      emit('plan-update', plan.state.currentPlan)
    }
    const merged: Message[] = []
    for (const m of raw) {
      if (m.role === 'assistant' && merged.length > 0 && merged[merged.length - 1].role === 'assistant') {
        const prev = merged[merged.length - 1]
        if (m.content) prev.content = (prev.content || '') + m.content
      } else {
        const msg: Message = { role: m.role as 'user' | 'assistant', content: m.content || '', timestamp: m.timestamp || '' }
        if (m.images) msg.images = m.images
        if (m.kind && (m.kind === 'chat' || m.kind === 'plan_discussion' || m.kind === 'plan_artifact' || m.kind === 'plan_interaction' || m.kind === 'system_notice')) msg.kind = m.kind
        if (m.plan) {
          msg.plan = m.plan
          if (!isFinalPlan(m.plan)) continue
        }
        merged.push(msg)
      }
    }
    const s = _state(sid)
    s.messages = merged
    if (restoredPlan) _syncPlanWizardMessages(s, restoredPlan)
    s.currentPlan = restoredPlan
    s.planState = restoredPlan?.status || 'idle'
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


function _stopAllStreamingMessages(s: SessionState) {
  s.messages = s.messages.map(m => m.streaming ? { ...m, streaming: false } : m)
}

function _resetStreaming(sid: string, reason: string) {
  const key = _cacheKey(sid, props.workspace)
  const s = _states.get(key)
  if (s) {
    s.isStreaming = false
    _stopAllStreamingMessages(s)
  }
  if (sid === activeSessionId.value) {
    isStreaming.value = false
  }
  console.warn(`[mini-ai] isStreaming reset: sid=${sid} reason=${reason}`)
  emit('status-change', sid, 'idle')
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
    _states.forEach((s) => {
      s.isStreaming = false
      _stopAllStreamingMessages(s)
    })
    isStreaming.value = false
    emit('status-change', activeSessionId.value, 'idle')
    // 重连后主动刷新配置，确保上下文 token 数与后端一致
    fetchConfig().catch(() => {})
    return
  }
  
  const sid = event.data?.session_id
  if (!sid) {
    if (event.event === 'error') console.warn('[mini-ai] WebSocket 全局错误', event.data?.error || event.data)
    return
  }
  const s = _state(sid)

  // 检测事件序号缺口（消息丢失检测）
  const seq = event.data?.seq
  if (seq !== undefined) {
    const expectedSeq = s._lastSeq + 1
    if (seq > expectedSeq + 1) {  // allow 1 event reorder, suppress gap=1 false alarm
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
  s._lastWsEventTime = Date.now()

  const isTerminal = event.event === 'done' || event.event === 'aborted' || event.event === 'error' || event.event === 'complete'

  // 🔧 简化：终端事件时，直接重置会话状态
  if (isTerminal) {
    s.isStreaming = false
    _stopAllStreamingMessages(s)
    s._currentContent = ''
    s._currentThinking = ''
    
    // 如果是当前活跃会话，重置全局 isStreaming
    if (sid === activeSessionId.value) {
      isStreaming.value = false
      console.log(`[mini-ai] terminal event reset isStreaming: sid=${sid} event=${event.event}`)
    }
    
    emit('status-change', sid, 'idle')
  }

  // Token 用量更新：terminal 事件携带的 usage 应总是上抛，避免会话切换后状态栏停在旧值
  // 但仅当事件来自当前活跃会话时才更新 UI 的 config（其他会话的 token 不应覆盖当前显示）
  if (isTerminal && sid === activeSessionId.value && event.data?.prompt_tokens !== undefined) {
    emit('config-update', {
      prompt_tokens: event.data.prompt_tokens,
      completion_tokens: event.data.completion_tokens || 0,
    })
  }

  if (sid === activeSessionId.value) {
    if (isTerminal) {
      if (_flushTimer !== null) { clearTimeout(_flushTimer); _flushTimer = null }
      _doFlush(sid)
      console.log(`[mini-ai] terminal event: sid=${sid} event=${event.event}`)
      fetchConfig()
      if (_streamingWatchdog !== null) { clearTimeout(_streamingWatchdog); _streamingWatchdog = null }
    } else {
      _scheduleFlush(sid)
      if (isStreaming.value) _startStreamingWatchdog(sid)
      // 流式过程中也更新 token（部分模型在 text 事件中携带中间 usage）
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
  const idx = _mainAssistantIndex(s)
  const current = idx >= 0 ? s.messages[idx] : undefined
  if (!current) return
  const hasContent = current.content || (current.tools && current.tools.length) || s._currentContent
  if (!hasContent) return
  s.messages[idx] = { ...current, streaming: false, content: s._currentContent || current.content }
  s._currentContent = ''
  s._currentThinking = ''
  s.messages.push({ role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs(), main: true })
}

function _updateUI(s: SessionState) {
  const key = _cacheKey(activeSessionId.value, props.workspace)
  const activeS = _states.get(key)
  if (s === activeS) {
    // Sync _currentContent into the main assistant message before full rebuild
    _syncMainContent(s)
    messages.value = [...s.messages]
    workflowStateRef.value = s.workflowState ? { ...s.workflowState, tasks: { ...s.workflowState.tasks } } : undefined
  }
}

// Stop streaming cursor on the last main-agent message (no teammate)
function _stopMainAgentStreaming(s: SessionState) {
  for (let i = s.messages.length - 1; i >= 0; i--) {
    const m = s.messages[i]
    if (m.role === 'assistant' && !m.teammate && m.streaming) {
      s.messages[i] = { ...m, streaming: false }
      break
    }
  }
}

function _processEvent(s: SessionState, event: WsEvent) {
  switch (event.event) {
    case 'thinking_start':
      {
        const tm = event.data.teammate || ''
        if (tm) {
          const tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (tmMsg) tmMsg.thinking = { chars: 0, elapsed: 0, content: '' }
        } else {
          s._currentThinking = ''
          _startNewAssistantMsg(s)
        }
      }
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
            _stopMainAgentStreaming(s)
            tmMsg = { role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs(), teammate: tm, teammateColor: _tmColor(tm) }
            s.messages.push(tmMsg)
          }
          tmMsg.thinking = { chars: buf.thinking.length, elapsed: 0, content: buf.thinking }
          _updateUI(s)
        } else {
          s._currentThinking += event.data.content || ''
          const idx = _mainAssistantIndex(s)
          const m = idx >= 0 ? s.messages[idx] : undefined
          if (m) {
            s.messages[idx] = { ...m, thinking: { chars: s._currentThinking.length, elapsed: 0, content: s._currentThinking } }
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
          const idx = _mainAssistantIndex(s)
          const m = idx >= 0 ? s.messages[idx] : undefined
          if (m && m.thinking) {
            s.messages[idx] = { ...m, thinking: { ...m.thinking, chars: event.data.chars || m.thinking.chars, elapsed: event.data.elapsed || 0 } }
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
            _stopMainAgentStreaming(s)
            tmMsg = { role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs(), teammate: tm, teammateColor: _tmColor(tm) }
            s.messages.push(tmMsg)
          }
          tmMsg.content = buf.content
          _updateUI(s)
        } else {
          const chunk = event.data.content || ''
          s._currentContent += chunk
          // 只修改 state 中的主 assistant 消息，不直接动全局 messages.value（避免跨会话串台）
          _syncMainContent(s)
          const idx = _mainAssistantIndex(s)
          if (idx >= 0 && s.messages[idx].kind === 'plan_discussion') {
            s.messages[idx] = { ...s.messages[idx], content: _stripPlanArtifactBlocks(s._currentContent) }
          }
        }
      }
      _scheduleScroll()
      // 仅当事件属于当前活跃会话时才调度刷新（避免跨会话串台）
      const _activeKey = _cacheKey(activeSessionId.value, props.workspace)
      if (s === _states.get(_activeKey)) {
        _scheduleFlush(activeSessionId.value)
      }
      return  // incremental: skip _scheduleFlush
    case 'tool_start':
      {
        const tm = event.data.teammate || ''
        if (tm) {
          let tmMsg = s.messages.slice().reverse().find(m => m.role === 'assistant' && m.teammate === tm && m.streaming)
          if (!tmMsg) {
            _stopMainAgentStreaming(s)
            tmMsg = { role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs(), teammate: tm, teammateColor: _tmColor(tm) }
            s.messages.push(tmMsg)
          }
          if (!tmMsg.tools) tmMsg.tools = []
          tmMsg.tools.push({ name: event.data.name || '?', args: event.data.args || '', result: '...', elapsed: 0, tool_call_id: event.data.tool_call_id || '' })
          _updateUI(s)
        } else {
          let idx = _mainAssistantIndex(s)
          if (idx < 0) {
            s.messages.push({ role: 'assistant', content: '', tools: [], streaming: true, timestamp: _localTs(), main: true })
            idx = s.messages.length - 1
          }
          const m = s.messages[idx]
          const tools = [...(m.tools || []), { name: event.data.name || '?', args: event.data.args || '', result: '...', elapsed: 0, tool_call_id: event.data.tool_call_id || '' }]
          s.messages[idx] = { ...m, tools }
          _updateUI(s)
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
        const idx = _mainAssistantIndex(s)
        const m = idx >= 0 ? s.messages[idx] : undefined
        if (m && m.tools && m.tools.length > 0) {
          let targetIndex = -1
          // 优先按 tool_call_id 精确匹配
          if (tcId) targetIndex = m.tools.findIndex((t: any) => t.tool_call_id === tcId)
          // 否则按 name + 占位符匹配
          if (targetIndex < 0) {
            const name = event.data.name || ''
            targetIndex = m.tools.findIndex((t: any) => t.name === name && t.result === '...')
          }
          // 最后兜底：找最后一个占位符
          if (targetIndex < 0) targetIndex = m.tools.findIndex((t: any) => t.result === '...')
          if (targetIndex < 0) targetIndex = m.tools.length - 1

          if (targetIndex >= 0) {
            const tools = [...m.tools]
            tools[targetIndex] = { ...tools[targetIndex], result: event.data.result || '', elapsed: event.data.elapsed || 0 }
            s.messages[idx] = { ...m, tools }
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
        _stopAllStreamingMessages(s)

        // complete/error 事件携带错误：始终显示，追加到主 assistant 内容
        if (event.data?.error) {
          console.error('[complete] LLM 返回错误:', event.data.error)
          let idx = _mainAssistantIndex(s)
          if (idx < 0) {
            s.messages.push({ role: 'assistant', content: '', timestamp: _localTs(), main: true })
            idx = s.messages.length - 1
          }
          const m = s.messages[idx]
          s.messages[idx] = { ...m, content: (m.content ? m.content + '\n\n' : '') + `⚠ ${event.data.error}`, streaming: false }
        }

        _updateUI(s)
      }
      break
    case 'plan_event':
      {
        const activeKey = _cacheKey(activeSessionId.value, props.workspace)
        const isActiveSession = s === _states.get(activeKey)
        if (isActiveSession) {
          plan.applyPlanEvent(event.data)
          s.planState = plan.state.planState
          s.currentPlan = plan.state.currentPlan
          planMode.value = plan.isPlanning.value || plan.state.planState === 'executing'
          emit('plan-mode-change', plan.state.planState)
          emit('plan-update', plan.state.currentPlan)
        } else {
          if (event.data?.plan !== undefined) s.currentPlan = event.data.plan
          if (event.data?.state) s.planState = event.data.state
          if (event.data?.kind === 'artifact.updated' && event.data.plan) s.planState = event.data.plan.status
          if (event.data?.kind === 'approval.required') s.planState = hasUnresolvedPlanInteractions(event.data.plan) ? 'awaiting_user' : 'awaiting_approval'
          if (event.data?.kind === 'execution.started') s.planState = 'executing'
          if (event.data?.kind === 'execution.completed') s.planState = 'completed'
          if (event.data?.kind === 'cancelled') s.planState = 'cancelled'
          if (event.data?.kind === 'approved') s.planState = 'approved'
        }
        if (event.data?.plan && ['artifact.updated', 'option.selected', 'approval.required', 'approved', 'execution.started', 'execution.completed'].includes(event.data.kind)) {
          _syncPlanWizardMessages(s, event.data.plan)
          const idx = _mainAssistantIndex(s)
          if (idx >= 0 && s.messages[idx].kind === 'plan_discussion') {
            s.messages[idx] = { ...s.messages[idx], streaming: false }
            s._currentContent = ''
          }
          _updateUI(s)
        }
        if (event.data?.error) {
          s.messages.push({ role: 'assistant', content: `⚠ ${event.data.error}`, timestamp: _localTs(), streaming: false })
          _updateUI(s)
        }
      }
      break
    case 'mode_change':
      planMode.value = event.data.mode === 'plan'
      emit('plan-mode-change', planMode.value ? 'planning' : 'idle')
      break
    case 'aborted':
      s._currentContent += '\n\n⚠ 已中断生成'
      _syncMainContent(s)
      _stopAllStreamingMessages(s)
      _updateUI(s)
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
        const errMsg = `⚠ ${event.data.error || '未知错误'}`
        _syncMainContent(s)
        let idx = _mainAssistantIndex(s)
        if (idx < 0) {
          s.messages.push({ role: 'assistant', content: '', timestamp: _localTs(), streaming: false, main: true })
          idx = s.messages.length - 1
        }
        const m = s.messages[idx]
        s.messages[idx] = { ...m, content: m.content ? m.content + '\n\n' + errMsg : errMsg, streaming: false }
        _stopAllStreamingMessages(s)
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
        tasks.forEach((t) => {
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
        tasks.forEach((t) => {
          const deps = t.depends_on && t.depends_on.length > 0 ? ` ← ${t.depends_on.join(', ')}` : ''
          content += `- **${t.id}** (${t.agent})${deps}\n`
        })
        s.messages.push({
          role: 'assistant',
          content,
          timestamp: _localTs(),
          streaming: false,
          workflow: { status: 'running', tasks: {} as WorkflowState['tasks'] }
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
            s.workflowState.tasks[taskId] = { id: taskId, agent, status: 'running', prompt, depends_on: [] }
          } else {
            s.workflowState.tasks[taskId].status = 'running'
          }
        }

        // 更新工作流消息
        const wfMsg = s.messages.slice().reverse().find(m => m.workflow)
        if (wfMsg && wfMsg.workflow) {
          const existing = wfMsg.workflow.tasks[taskId]
          wfMsg.workflow.tasks[taskId] = {
            id: taskId,
            agent,
            status: 'running',
            prompt: existing?.prompt || prompt,
            depends_on: existing?.depends_on || [],
            result: existing?.result,
          }
          const runningCount = Object.values(wfMsg.workflow.tasks).filter((t) => t.status === 'running').length
          const doneCount = Object.values(wfMsg.workflow.tasks).filter((t) => t.status === 'done').length
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
        const terminalStatus: WorkflowTaskStatus = status === 'done' || status === 'skipped' ? status : 'failed'
        const result = event.data.result_preview || event.data.error || ''

        // 更新会话级别工作流状态
        if (s.workflowState && s.workflowState.tasks[taskId]) {
          s.workflowState.tasks[taskId].status = terminalStatus
          s.workflowState.tasks[taskId].result = result
        }

        // 更新工作流消息
        const wfMsg = s.messages.slice().reverse().find(m => m.workflow)
        if (wfMsg && wfMsg.workflow) {
          const existing = wfMsg.workflow.tasks[taskId]
          wfMsg.workflow.tasks[taskId] = {
            id: taskId,
            agent: existing?.agent || '',
            status: terminalStatus,
            prompt: existing?.prompt || '',
            depends_on: existing?.depends_on || [],
            result: result.slice(0, 100)
          }
          const runningCount = Object.values(wfMsg.workflow.tasks).filter((t) => t.status === 'running').length
          const doneCount = Object.values(wfMsg.workflow.tasks).filter((t) => t.status === 'done').length
          const failedCount = Object.values(wfMsg.workflow.tasks).filter((t) => t.status === 'failed').length
          wfMsg.workflow.status = runningCount > 0 ? 'running' : (failedCount > 0 ? 'failed' : 'done')
          _updateUI(s)
        }
        // 添加任务完成通知
        const icon = terminalStatus === 'done' ? '✅' : terminalStatus === 'skipped' ? '⏭' : '❌'
        const statusText = terminalStatus === 'done' ? '完成' : terminalStatus === 'skipped' ? '跳过' : '失败'
        s.messages.push({
          role: 'assistant',
          content: `${icon} **${taskId}** ${statusText}${result ? `: ${result.slice(0, 100)}${result.length > 100 ? '...' : ''}` : ''}`,
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
        _stopMainAgentStreaming(s)
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

async function doExport() {
  try {
    await exportSession(activeSessionId.value, props.workspace || undefined, 0, false, false)
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
    ensureWs().finally(() => startPlan(activeSessionId.value, props.workspace))
    return
  }
  if (text === '/act') {
    const current = plan.state.currentPlan
    if (current) ensureWs().finally(() => approvePlan(activeSessionId.value, current.plan_id, current.revision, props.workspace))
    else s.messages = [...s.messages, { role: 'assistant', content: '⚠ 当前没有可审批的计划。先输入 /plan 开始规划。', timestamp: _localTs() }]
    _updateUI(s)
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
      const resp = await getTools(activeSessionId.value, props.workspace || undefined)
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
  
  s.messages = [...s.messages, userMsg, { role: 'assistant', content: '', tools: [], streaming: true, timestamp: '', main: true, kind: (plan.isPlanning.value || plan.state.planState === 'awaiting_approval') ? 'plan_discussion' : 'chat' }]
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
  try { wsOk = await ensureWs() } catch {}
  if (plan.isPlanning.value || plan.state.planState === 'awaiting_approval') sendPlanMessage(text, sid, props.workspace, userMsg.images)
  else wsChat(text, sid, props.workspace, userMsg.images)
  if (!wsOk) {
    const idx = _mainAssistantIndex(s)
    if (idx >= 0) {
      const m = s.messages[idx]
      s.messages[idx] = { ...m, content: '⚠ WebSocket 未连接，消息已加入待发送队列，将在重连后自动发送。', streaming: false }
    }
    s.isStreaming = false
    isStreaming.value = false
    _updateUI(s)
    emit('status-change', sid, 'disconnected')
  }
}

async function handleRetry(msgIndex: number) {
  const sid = activeSessionId.value
  const s = _state(sid)
  let userMsg: Message | undefined
  for (let i = msgIndex - 1; i >= 0; i--) {
    if (s.messages[i]?.role === 'user') {
      userMsg = s.messages[i]
      break
    }
  }
  if (!userMsg) return

  s.messages.splice(msgIndex, 1, { role: 'assistant', content: '', tools: [], streaming: true, timestamp: '', main: true })
  s._currentContent = ''
  s._currentThinking = ''
  s.isStreaming = true
  isStreaming.value = true
  _updateUI(s)
  emit('status-change', sid, 'generating')
  _startStreamingWatchdog(sid)

  let wsOk = false
  try { wsOk = await ensureWs() } catch {}
  if (plan.isPlanning.value || plan.state.planState === 'awaiting_approval') sendPlanMessage(userMsg.content || '', sid, props.workspace, userMsg.images)
  else wsChat(userMsg.content || '', sid, props.workspace, userMsg.images)
  if (!wsOk) {
    const idx = _mainAssistantIndex(s)
    if (idx >= 0) {
      const m = s.messages[idx]
      s.messages[idx] = { ...m, content: '⚠ WebSocket 未连接，消息已加入待发送队列，将在重连后自动发送。', streaming: false }
    }
    s.isStreaming = false
    isStreaming.value = false
    _updateUI(s)
    emit('status-change', sid, 'disconnected')
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
    _syncMainContent(s)
    _stopAllStreamingMessages(s)
    messages.value = [...s.messages]
  }
}

function approveCurrentPlan() {
  const current = plan.state.currentPlan
  if (!current) return
  ensureWs().finally(() => approvePlan(activeSessionId.value, current.plan_id, current.revision, props.workspace))
}

function cancelCurrentPlan() {
  const current = plan.state.currentPlan
  ensureWs().finally(() => cancelPlan(activeSessionId.value, current?.plan_id, props.workspace))
}

function openPlanOptionsDialog() {
  const key = _cacheKey(activeSessionId.value, props.workspace)
  const current = plan.state.currentPlan || _states.get(key)?.currentPlan
  if (!current?.options?.length) return
  planDialog.value = {
    visible: true,
    mode: 'option',
    title: '选择整体实施方案',
    subtitle: current.summary || current.goal,
    options: current.options,
    allowMultiple: false,
    selectedIds: current.selected_option_id ? [current.selected_option_id] : (current.options || []).filter(o => o.recommended).slice(0, 1).map(o => o.id),
    customValue: '',
  }
}

function openPlanDecisionDialog(payload: PlanDecisionOpenPayload) {
  planDialog.value = {
    visible: true,
    mode: 'decision',
    title: payload.decision.title,
    subtitle: payload.decision.description,
    options: payload.decision.options || [],
    allowMultiple: !!payload.decision.allow_multiple,
    selectedIds: payload.decision.selected_option_ids?.length
      ? [...payload.decision.selected_option_ids]
      : (payload.decision.options || []).filter(o => o.recommended).slice(0, payload.decision.allow_multiple ? undefined : 1).map(o => o.id),
    customValue: payload.decision.custom_value || '',
    decision: payload.decision,
    stepId: payload.stepId,
    stepTitle: payload.stepTitle,
  }
}

function closePlanDialog() {
  planDialog.value = { ...planDialog.value, visible: false }
}

function suppressAutoOpen(planId?: string, revision?: number) {
  if (!planId || !revision) return
  suppressedApprovalKeys.add(`${planId}:${revision}`)
}

function _markInteractionSubmitted(interactionId?: string) {
  if (!interactionId) return
  completedInteractionIds.add(interactionId)
  const key = _cacheKey(activeSessionId.value, props.workspace)
  const s = _states.get(key)
  if (!s) return
  const idx = s.messages.findIndex(m => m.kind === 'plan_interaction' && m.planInteraction?.id === interactionId)
  if (idx >= 0 && s.messages[idx].planInteraction) {
    s.messages[idx] = { ...s.messages[idx], planInteraction: { ...s.messages[idx].planInteraction!, completed: true } }
    _updateUI(s)
  }
}

function _buildInteractionInstruction(interaction: PlanInteraction, selectedIds: string[], customValue: string): string {
  if (interaction.type === 'top_option') {
    const current = plan.state.currentPlan
    const optionId = selectedIds[0]
    const option = current?.options?.find(o => o.id === optionId)
    if (optionId && !customValue) {
      return `我选择整体方案 ${optionId}${option ? `（${option.title}）` : ''}。请基于这个方案重新生成后续计划：更新 selected_option_id，并同步重写 summary、steps、risks、validation_strategy。还要重新检查该新方案的每个步骤是否存在方案内可选实现/参数/取舍；如果有，必须写入对应 step.decisions，提供单选或多选选项、推荐项和“其他想法”入口；如果没有，也要确保步骤描述足够明确可执行。不要只标记选中项。`
    }
    const pieces: string[] = ['整体方案选择']
    if (optionId) pieces.push(`选择: ${optionId}`)
    if (customValue) pieces.push(`其他想法: ${customValue}`)
    return `请按我的交互选择修订计划：${pieces.join('；')}。如果这是一个新方案，也要同步重写 summary、steps、risks、validation_strategy，并重新生成该方案内需要用户选择的步骤决策。`
  }

  const pieces: string[] = []
  if (interaction.stepId) pieces.push(`步骤 ${interaction.stepId}${interaction.stepTitle ? `（${interaction.stepTitle}）` : ''}`)
  if (interaction.decisionId) pieces.push(`决策 ${interaction.decisionId}`)
  if (selectedIds.length) pieces.push(`选择: ${selectedIds.join(', ')}`)
  if (customValue) pieces.push(`其他想法: ${customValue}`)
  return `请按我的交互选择修订计划：${pieces.join('；')}。请把该决策写回对应 step.decisions 的 selected_option_ids/custom_value，并继续检查后续步骤是否还有需要我选择的方案内决策；如果没有，请给出可最终审批的计划。`
}

function submitPlanInteraction(payload: { interaction: PlanInteraction; selectedIds: string[]; customValue: string }) {
  const current = plan.state.currentPlan
  if (!current) return
  _markInteractionSubmitted(payload.interaction.id)
  suppressAutoOpen(current.plan_id, current.revision + 1)
  if (payload.interaction.type === 'step_decision' && payload.interaction.stepId && payload.interaction.decisionId) {
    ensureWs().finally(() => applyPlanDecision(
      activeSessionId.value,
      current.plan_id,
      current.revision,
      payload.interaction.stepId!,
      payload.interaction.decisionId!,
      payload.selectedIds,
      payload.customValue,
      props.workspace,
    ))
    return
  }
  sendMessage(_buildInteractionInstruction(payload.interaction, payload.selectedIds, payload.customValue))
}

function confirmPlanDialog(payload: PlanChoiceConfirmPayload) {
  const dialog = planDialog.value
  closePlanDialog()
  const current = plan.state.currentPlan
  if (!current) return
  const interaction: PlanInteraction = dialog.mode === 'option'
    ? {
        id: `${current.plan_id}:${current.revision}:top-option-dialog`,
        planId: current.plan_id,
        revision: current.revision,
        type: 'top_option',
        title: '选择整体实施方案',
        description: current.summary || current.goal,
        options: current.options || [],
        allowMultiple: false,
        selectedIds: current.selected_option_id ? [current.selected_option_id] : [],
      }
    : {
        id: `${current.plan_id}:${current.revision}:decision-dialog:${dialog.stepId || ''}:${dialog.decision?.id || ''}`,
        planId: current.plan_id,
        revision: current.revision,
        type: 'step_decision',
        title: dialog.decision?.title || dialog.title,
        description: dialog.decision?.description || dialog.subtitle,
        options: dialog.decision?.options || [],
        allowMultiple: !!dialog.decision?.allow_multiple,
        selectedIds: [...(dialog.decision?.selected_option_ids || [])],
        customValue: dialog.decision?.custom_value || '',
        stepId: dialog.stepId,
        stepTitle: dialog.stepTitle,
        decisionId: dialog.decision?.id,
      }
  submitPlanInteraction({ interaction, selectedIds: payload.selectedIds, customValue: payload.customValue })
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

defineExpose({
  useSkill,
  switchToSession,
  activeSessionId,
  planMode,
  openPlanOptionsDialog,
  openPlanDecisionDialog,
  getCurrentWorkflowState,
  getCurrentPlan: () => plan.state.currentPlan,
  getPlanState: () => plan.state.planState,
})

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

  <PlanModeBanner :state="plan.state.planState" />
  <div class="chat-view" ref="chatContainer" @scroll="_onChatScroll">
    <div class="messages" v-if="messages.length === 0">
      <div class="empty-state">
        <div class="empty-icon">m</div>
        <h2 class="empty-title">mini_ai<span class="empty-dot">.</span></h2>
        <p class="empty-sub">你的 AI 编程伙伴</p>

      </div>
    </div>
    <div class="messages">
      <MessageItem
        v-for="(msg, i) in messages"
        :key="i"
        :message="msg"
        :style="{ animationDelay: `${Math.min(i * 0.04, 0.6)}s` }"
        class="msg-anim"
        @retry="handleRetry(i)"
        @open-plan-options="openPlanOptionsDialog"
        @open-plan-decision="openPlanDecisionDialog"
        @submit-plan-interaction="submitPlanInteraction"
      />

    </div>
    <button v-if="showScrollBottom" class="scroll-bottom-btn" @click="scrollToBottom" title="滚动到底部">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>
  </div>
  <div class="input-area">
    <PlanApprovalBar :visible="effectiveAwaitingApproval" :plan="plan.state.currentPlan" :disabled="isStreaming" @approve="approveCurrentPlan" @cancel="cancelCurrentPlan" />
    <InputBar v-model="draftText" :disabled="isStreaming" :is-streaming="isStreaming" :mode="effectiveInputMode" @send="sendMessage" @stop="stopGeneration" />
    <PlanChoiceDialog
      :visible="planDialog.visible"
      :mode="planDialog.mode"
      :title="planDialog.title"
      :subtitle="planDialog.subtitle"
      :options="planDialog.options"
      :allow-multiple="planDialog.allowMultiple"
      :selected-ids="planDialog.selectedIds"
      :custom-value="planDialog.customValue"
      :step-title="planDialog.stepTitle"
      @close="closePlanDialog"
      @confirm="confirmPlanDialog"
    />
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
  background: radial-gradient(circle at 58% -8%, color-mix(in srgb, var(--accent) 12%, transparent), transparent 34%), linear-gradient(180deg, color-mix(in srgb, var(--bg) 42%, transparent), transparent 36%);
  position: relative;
}

.chat-container::before {
  content: '';
  position: absolute;
  inset: 14px 18px 76px;
  border: 1px solid color-mix(in srgb, var(--border-light) 20%, transparent);
  border-radius: 28px;
  pointer-events: none;
  background: linear-gradient(180deg, rgba(255,255,255,.018), transparent 26%);
  mask-image: linear-gradient(to bottom, #000 0%, transparent 88%);
}

.chat-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--surface-hairline);
  background: linear-gradient(180deg, color-mix(in srgb, var(--bg) 76%, transparent), color-mix(in srgb, var(--bg-card) 44%, transparent));
  backdrop-filter: blur(14px);
  position: relative;
  z-index: 2;
}

.export-btn {
  padding: 0.38rem 0.72rem;
  border: 1px solid var(--surface-hairline);
  border-radius: 999px;
  background: var(--surface-control);
  color: var(--fg-muted);
  font-size: 0.72rem;
  font-family: var(--font-mono);
  font-weight: 800;
  cursor: pointer;
  transition: all .16s var(--ease-out);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.035);
}
.export-btn:hover { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); transform: translateY(-1px); }



.chat-view {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 0 0.6rem;
  position: relative;
  z-index: 1;
}

.msg-anim {
  animation: fadeInUp 0.35s ease both;
}

.messages {
  padding: 0 3.2%;
  position: relative;
  z-index: 1;
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
  width: 86px;
  height: 86px;
  border-radius: 28px;
  border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border));
  background: radial-gradient(circle at 32% 24%, var(--accent-soft), transparent 48%), var(--surface-panel);
  color: var(--accent);
  font-family: var(--font-display);
  font-weight: 800;
  font-size: 2.4rem;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1.2rem;
  box-shadow: var(--glow-accent);
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
  bottom: 104px;
  left: 50%;
  transform: translateX(-50%);
  width: 42px;
  height: 42px;
  border-radius: 999px;
  background: var(--surface-control);
  border: 1px solid color-mix(in srgb, var(--accent) 42%, var(--border));
  color: var(--accent);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-card);
  backdrop-filter: blur(14px) saturate(1.08);
  transition: all .16s var(--ease-out);
  z-index: 1000;
}

.scroll-bottom-btn:hover {
  border-color: var(--accent);
  box-shadow: var(--glow-accent);
  transform: translateX(-50%) translateY(-2px);
}

.scroll-bottom-btn svg {
  width: 22px;
  height: 22px;
}





.input-area {
  flex-shrink: 0;
  border-top: 1px solid color-mix(in srgb, var(--border) 42%, transparent);
  background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--bg) 90%, transparent) 24%);
  backdrop-filter: blur(16px);
  position: relative;
  z-index: 2;
}




</style>
