import type { PlanArtifact, PlanEventData } from './plan/types'

const USERNAME_KEY = 'mini-ai-username'
const _FETCH_TIMEOUT = 8000


const _origFetch = window.fetch.bind(window)

export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonObject | JsonArray
export interface JsonObject { [key: string]: JsonValue | undefined }
export interface JsonArray extends Array<JsonValue> {}

interface ApiErrorBody extends JsonObject {
  error?: JsonValue
  message?: JsonValue
}

export class ApiError extends Error {
  status: number
  data: JsonValue
  url: string

  constructor(message: string, status: number, data: JsonValue, url: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
    this.url = url
  }
}

function _asApiErrorBody(data: JsonValue): ApiErrorBody | null {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null
  return data as ApiErrorBody
}

async function _parseErrorBody(resp: Response): Promise<JsonValue> {
  const text = await resp.text().catch(() => '')
  if (!text) return null
  try { return JSON.parse(text) as JsonValue } catch { return text }
}

async function _fetch(url: string, init?: RequestInit & { timeout?: number }): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), init?.timeout ?? _FETCH_TIMEOUT)
  try {
    const resp = await _origFetch(url, { ...init, signal: controller.signal })
    if (!resp.ok) {
      const data = await _parseErrorBody(resp)
      const errorData = _asApiErrorBody(data)
      const message = typeof data === 'string'
        ? data
        : typeof errorData?.error === 'string'
          ? errorData.error
          : typeof errorData?.message === 'string'
            ? errorData.message
            : `HTTP ${resp.status}`
      throw new ApiError(message, resp.status, data, url)
    }
    return resp
  } finally {
    clearTimeout(timer)
  }
}

function _json<T>(resp: Response): Promise<T> {
  return resp.json() as Promise<T>
}

async function _withFallbackError<T>(operation: () => Promise<T>, fallback: string): Promise<T> {
  try {
    return await operation()
  } catch (e) {
    if (e instanceof ApiError) throw new Error(e.message || fallback)
    throw e
  }
}

function _parseWsEvent(raw: string): DisplayWireEvent {
  return JSON.parse(raw) as DisplayWireEvent
}


export function getUsername(): string {
  return localStorage.getItem(USERNAME_KEY) || ''
}

export function setUsername(name: string) {
  localStorage.setItem(USERNAME_KEY, name)
}

export function hasUsername(): boolean {
  return !!localStorage.getItem(USERNAME_KEY)
}

export type WsEventName =
  | 'connected' | 'reconnected' | 'disconnected' | 'pong'
  | 'llm_round_start' | 'llm_round_end'
  | 'thinking_start' | 'thinking' | 'thinking_end'
  | 'text' | 'tool_start' | 'tool_result' | 'todos'
  | 'done' | 'complete' | 'plan_event' | 'mode_change' | 'aborted'
  | 'teammate_status' | 'blackboard_update' | 'inbox_message'
  | 'info' | 'error'
  | 'workflow_start' | 'task_start' | 'task_end' | 'workflow_end'
  | 'agent_start'

export type WorkflowTaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'
export type WorkflowStatus = 'idle' | 'running' | 'done' | 'failed'
export type TeammateStatus = 'idle' | 'working' | 'offline' | 'shutdown'

export interface TeammateInfo {
  name: string
  role: string
  status: TeammateStatus
}

export interface TeamStatusResponse {
  teammates: TeammateInfo[]
  has_team: boolean
}

export interface BlackboardEntry {
  value: string
  author: string
  ts: number
}

export interface BlackboardResponse {
  entries: Record<string, BlackboardEntry>
  has_blackboard: boolean
}

export interface TeamMutationResponse {
  status?: 'ok'
  message?: string
  error?: string
}

export interface TeammateStatusData extends WsBaseData {
  name: string
  status: TeammateStatus
}

export interface BlackboardUpdateData extends WsBaseData {
  key: string
  author: string
}

export interface InboxMessageData extends WsBaseData {
  to: string
  from: string
  count: number
}

export interface WorkflowTaskInfo {
  id: string
  agent: string
  prompt: string
  depends_on: string[]
}

export interface WorkflowTaskState extends WorkflowTaskInfo {
  status: WorkflowTaskStatus
  result?: string
}

export interface WorkflowState {
  status: WorkflowStatus
  tasks: Record<string, WorkflowTaskState>
  elapsed?: number
  completed?: number
  failed?: number
  total?: number
}

export interface WorkflowStartData extends WsBaseData {
  tasks: WorkflowTaskInfo[]
  total: number
}

export interface WorkflowTaskStartData extends WsBaseData {
  id: string
  agent: string
  prompt: string
}

export interface WorkflowTaskEndData extends WsBaseData {
  id: string
  status: WorkflowTaskStatus
  result_preview?: string
  error?: string
}

export interface WorkflowEndData extends WsBaseData {
  elapsed?: number
  completed?: number
  failed?: number
  total?: number
}

export interface WsBaseData {
  session_id?: string
  agent_id?: string
  teammate?: string
  seq?: number
  prompt_tokens?: number
  completion_tokens?: number
  event_id?: string
}

export type DisplayWireEvent = WsEvent
export type TeamWsEvent = Extract<WsEvent, { event: 'teammate_status' | 'blackboard_update' }>
export type WorkflowWsEvent = Extract<WsEvent, { event: 'workflow_start' | 'task_start' | 'task_end' | 'workflow_end' }>

export type WsEvent =
  | { event: 'connected' | 'reconnected'; data: WsBaseData }
  | { event: 'disconnected'; data: WsBaseData & { code?: number; reason?: string } }
  | { event: 'pong'; data: WsBaseData }
  | { event: 'llm_round_start'; data: WsBaseData & { model?: string } }
  | { event: 'llm_round_end'; data: WsBaseData & { model?: string; elapsed?: number } }
  | { event: 'thinking_start'; data: WsBaseData }
  | { event: 'thinking'; data: WsBaseData & { content: string } }
  | { event: 'thinking_end'; data: WsBaseData & { chars?: number; elapsed?: number } }
  | { event: 'text'; data: WsBaseData & { content: string } }
  | { event: 'tool_start'; data: WsBaseData & { name: string; args?: string; tool_call_id?: string } }
  | { event: 'tool_result'; data: WsBaseData & { name: string; result: string; elapsed?: number; tool_call_id?: string } }
  | { event: 'todos'; data: WsBaseData & { content: string } }
  | { event: 'done' | 'complete'; data: WsBaseData & { error?: string; error_context?: JsonValue } }
  | { event: 'plan_event'; data: WsBaseData & PlanEventData }
  | { event: 'mode_change'; data: WsBaseData & { mode?: string } }
  | { event: 'aborted'; data: WsBaseData & { error?: string } }
  | { event: 'teammate_status'; data: TeammateStatusData }
  | { event: 'blackboard_update'; data: BlackboardUpdateData }
  | { event: 'inbox_message'; data: InboxMessageData }
  | { event: 'info'; data: WsBaseData & { message: string } }
  | { event: 'error'; data: WsBaseData & { error: string } }
  | { event: 'workflow_start'; data: WorkflowStartData }
  | { event: 'task_start'; data: WorkflowTaskStartData }
  | { event: 'task_end'; data: WorkflowTaskEndData }
  | { event: 'workflow_end'; data: WorkflowEndData }
  | { event: 'agent_start'; data: WsBaseData & { agent_type: string; task?: string; role?: string; max_turns?: number } }

export interface ModelInfo {
  name: string
  model: string
}

export interface ModelsResponse {
  active: string
  active_name: string
  models: ModelInfo[]
}

export interface SwitchModelResponse extends ApiMutationResponse {
  status?: 'ok'
  active_name: string
  model: string
}

export interface ResetChatResponse extends ApiMutationResponse {
  status?: 'ok'
  session_id: string
}

export interface ConfigResponse {
  model: string
  context_length: number
  prompt_tokens: number
  completion_tokens: number
  system_prompt_tokens: number
  history_count: number
  session_id: string
  username: string
}

export interface CommandInfo {
  name: string
  desc: string
  has_arg: boolean
  arg_name?: string
  options?: { value: string }[]
}

export interface CommandsResponse {
  commands: CommandInfo[]
}

export type TodoStatus = 'pending' | 'in_progress' | 'completed'

export interface TodoItem {
  id: number
  content: string
  status: TodoStatus
}

export interface TodosResponse {
  todos: TodoItem[]
}

export interface ToolCallDisplay {
  name: string
  args: string
  result: string
  elapsed: number
  tool_call_id?: string
}

export interface ToolFunctionCallPayload {
  name?: string
  arguments?: string
}

export interface HistoryToolCall {
  id?: string
  type?: string
  function?: ToolFunctionCallPayload
  _result?: string
}

export interface ThinkingSnapshot {
  chars?: number
  elapsed?: number
  content?: string
}

export interface HistoryMessage {
  tool_calls?: HistoryToolCall[]
  role: string
  content?: string
  images?: ImageData[]
  thinking?: ThinkingSnapshot | string
  timestamp?: string
  kind?: string
  plan?: PlanArtifact
}

export interface HistoryResponse {
  session_id: string
  history: HistoryMessage[]
  current_plan?: PlanArtifact | null
}

export interface SessionInfo {
  session_id: string
  name: string
  message_count: number
  preview: string
  created_at: string
  updated_at: string
  status: 'idle' | 'generating' | 'connected' | 'disconnected'
}

export interface SessionsResponse {
  sessions: SessionInfo[]
}

export interface WorkspaceInfo {
  name: string
  project_path: string
}

export interface WorkspacesResponse {
  workspaces: WorkspaceInfo[]
  active: string
}

export interface ApiMutationResponse {
  status?: 'ok'
  message?: string
  error?: string
}

export interface DeleteSessionResponse extends ApiMutationResponse {
  status?: 'ok'
}

export interface BatchDeleteSessionsResponse extends ApiMutationResponse {
  deleted: number
}

export interface RenameSessionResponse extends ApiMutationResponse {
  name?: string
}

export interface WorkspaceMutationResponse extends ApiMutationResponse {
  project_path?: string
  session_id?: string
}

export type WorkspaceActionResponse = WorkspaceMutationResponse
export type TeamActionResponse = TeamMutationResponse

export interface SystemPromptResponse {
  system_prompt: string
  chars: number
  tokens: number
}

export interface SkillsListResponse {
  skills: SkillInfo[]
}

export interface RemovedWorkspacesResponse {
  removed: RemovedWorkspaceInfo[]
}

export interface BreadcrumbItem {
  name: string
  path: string
}

export interface FileItem {
  name: string
  type: 'dir' | 'file'
  path: string
  size?: number
  language?: string
  modified?: string
}

export interface FileListResponse extends ApiMutationResponse {
  root?: string
  current_path?: string
  breadcrumb?: BreadcrumbItem[]
  items?: FileItem[]
  truncated?: boolean
}

export interface FileReadTextResponse extends ApiMutationResponse {
  path?: string
  language?: string
  content?: string
  offset?: number
  limit?: number
  total_lines?: number
  has_more?: boolean
  size?: number
  modified?: string
  is_binary: false
  is_image?: false
}

export interface FileReadBinaryResponse extends ApiMutationResponse {
  path?: string
  is_binary: true
  is_image?: boolean
  mime_type?: string
  size?: number
  modified?: string
  language?: string
}

export type FileReadResponse = FileReadTextResponse | FileReadBinaryResponse

export interface FileSearchResponse extends ApiMutationResponse {
  results?: FileItem[]
  query?: string
  scanned?: number
  truncated?: boolean
}

interface SessionRequestBody extends UsernamePayload {
  workspace?: string
}

interface SessionIdRequestBody extends SessionRequestBody {
  session_id: string
}

interface BatchDeleteSessionsRequestBody extends SessionRequestBody {
  session_ids: string[]
}

interface RenameSessionRequestBody extends SessionIdRequestBody {
  name: string
}

interface CreateWorkspaceRequestBody extends UsernamePayload {
  name: string
  project_path?: string
}

function _username(): string {
  return getUsername() || 'default'
}

interface UsernamePayload {
  username?: string
}

interface WsSessionPayload extends UsernamePayload {
  session_id?: string
  workspace?: string
}

interface SwitchModelRequestBody extends WsSessionPayload {
  name: string
}

interface ResetChatRequestBody extends WsSessionPayload {}

interface WsClientMessage {
  type: string
  client_message_id?: string
}

export interface ChatWsMessage extends WsClientMessage, WsSessionPayload {
  type: 'chat'
  message: string
  images?: ImageData[]
}

export interface AbortWsMessage extends WsClientMessage, WsSessionPayload {
  type: 'abort'
}

export interface PlanStartWsMessage extends WsClientMessage, WsSessionPayload {
  type: 'plan.start'
  goal: string
}

export interface PlanMessageWsMessage extends WsClientMessage, WsSessionPayload {
  type: 'plan.message'
  message: string
  images?: ImageData[]
}

export interface PlanSelectOptionWsMessage extends WsClientMessage, UsernamePayload {
  type: 'plan.select_option'
  session_id: string
  plan_id: string
  option_id: string
  workspace?: string
}

export interface PlanApproveWsMessage extends WsClientMessage, UsernamePayload {
  type: 'plan.approve'
  session_id: string
  plan_id: string
  revision: number
  workspace?: string
}

export interface PlanApplyDecisionWsMessage extends WsClientMessage, UsernamePayload {
  type: 'plan.apply_decision'
  session_id: string
  plan_id: string
  revision: number
  step_id: string
  decision_id: string
  selected_option_ids: string[]
  custom_value: string
  workspace?: string
}

export interface PlanReviseWsMessage extends WsClientMessage, UsernamePayload {
  type: 'plan.revise'
  session_id: string
  plan_id: string
  message: string
  workspace?: string
}

export interface PlanCancelWsMessage extends WsClientMessage, UsernamePayload {
  type: 'plan.cancel'
  session_id: string
  plan_id: string
  workspace?: string
}

type QueueableWsMessage = ChatWsMessage | PlanStartWsMessage | PlanMessageWsMessage | PlanSelectOptionWsMessage | PlanApproveWsMessage | PlanApplyDecisionWsMessage
type SendableWsMessage = QueueableWsMessage | AbortWsMessage | PlanReviseWsMessage | PlanCancelWsMessage

function _usernameBody(): UsernamePayload {
  const u = getUsername()
  return u ? { username: u } : {}
}

// ── WebSocket ──

let _ws: WebSocket | null = null
let _wsConnected = false
const _eventHandlers: ((event: WsEvent) => void)[] = []

// Pending messages queue for offline resilience
interface PendingWsMessage {
  id: string
  createdAt: number
  payload: QueueableWsMessage
}
const _pendingMessages: PendingWsMessage[] = []
const _PENDING_MAX = 50
const _PENDING_TTL_MS = 2 * 60 * 1000
const _QUEUEABLE_WS_TYPES = new Set<QueueableWsMessage['type']>(['chat', 'plan.start', 'plan.message', 'plan.select_option', 'plan.approve', 'plan.apply_decision'])

function _isQueueableWsMessage(msg: SendableWsMessage): msg is QueueableWsMessage {
  return _QUEUEABLE_WS_TYPES.has(msg.type as QueueableWsMessage['type'])
}

// 导出连接状态查询函数
export function isWsConnected(): boolean {
  return _wsConnected && _ws !== null && _ws.readyState === WebSocket.OPEN
}

// 重连配置
const _WS_RECONNECT_DELAY = 1000
const _WS_MAX_RECONNECT_DELAY = 30000
let _wsReconnectAttempts = 0
let _wsReconnectTimer: ReturnType<typeof setTimeout> | null = null
let _wsManuallyClosed = false

// 心跳配置
const _WS_HEARTBEAT_INTERVAL = 30000  // 30秒
const _WS_HEARTBEAT_TIMEOUT = 10000   // 10秒超时
let _wsHeartbeatTimer: ReturnType<typeof setTimeout> | null = null
let _wsHeartbeatTimeoutTimer: ReturnType<typeof setTimeout> | null = null
let _wsLastPongTime = 0

function _wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/api/chat/ws`
}

function _startHeartbeat() {
  _stopHeartbeat()
  _wsLastPongTime = Date.now()
  
  _wsHeartbeatTimer = setInterval(() => {
    if (!_ws || _ws.readyState !== WebSocket.OPEN) {
      _stopHeartbeat()
      return
    }
    
    // 发送 ping
    _ws.send(JSON.stringify({ type: 'ping' }))
    
    // 设置超时检测
    _wsHeartbeatTimeoutTimer = setTimeout(() => {
      const elapsed = Date.now() - _wsLastPongTime
      if (elapsed > _WS_HEARTBEAT_TIMEOUT) {
        console.warn(`[WS] 心跳超时 (${elapsed}ms)，关闭连接`)
        _ws?.close(1000, 'heartbeat_timeout')
      }
    }, _WS_HEARTBEAT_TIMEOUT)
  }, _WS_HEARTBEAT_INTERVAL)
}

function _stopHeartbeat() {
  if (_wsHeartbeatTimer) {
    clearInterval(_wsHeartbeatTimer)
    _wsHeartbeatTimer = null
  }
  if (_wsHeartbeatTimeoutTimer) {
    clearTimeout(_wsHeartbeatTimeoutTimer)
    _wsHeartbeatTimeoutTimer = null
  }
}

let _wsGeneration = 0

export async function ensureWs(): Promise<boolean> {
  if (_ws && _wsConnected && _ws.readyState === WebSocket.OPEN) return true

  if (_ws && _ws.readyState !== WebSocket.CLOSED) {
    _ws.close(1000, 'reconnect')
    await new Promise<void>(r => { _ws!.onclose = () => r(); setTimeout(r, 1000) })
  }

  const gen = ++_wsGeneration
  _wsManuallyClosed = false

  return new Promise<boolean>((resolve) => {
    const ws = new WebSocket(_wsUrl())
    _ws = ws
    _wsConnected = false

    const timer = setTimeout(() => {
      if (!_wsConnected) {
        ws.close()
        resolve(false)
      }
    }, 5000)

    ws.onopen = () => {
      clearTimeout(timer)
      _wsConnected = true
      
      // 判断是重连还是首次连接（在重置计数之前判断）
      const isReconnect = _wsReconnectAttempts > 0
      
      const u = getUsername()
      if (u) ws.send(JSON.stringify({ type: 'login', username: u }))

      // 重置重连计数
      _wsReconnectAttempts = 0
      _flushPendingMessages()
      
      // 启动心跳
      _startHeartbeat()
      
      // 通知前端连接已恢复
      for (const handler of _eventHandlers) {
        if (isReconnect) {
          handler({ event: 'reconnected', data: {} })
        } else {
          handler({ event: 'connected', data: {} })
        }
      }
      
      resolve(true)
    }

    ws.onmessage = (e) => {
      if (gen !== _wsGeneration) return
      try {
        const evt = _parseWsEvent(e.data)
        
        // 处理 pong 响应
        if (evt.event === 'pong') {
          _wsLastPongTime = Date.now()
          if (_wsHeartbeatTimeoutTimer) {
            clearTimeout(_wsHeartbeatTimeoutTimer)
            _wsHeartbeatTimeoutTimer = null
          }
          return
        }
        
        for (const handler of _eventHandlers) {
          handler(evt)
        }
      } catch {}
    }

    ws.onerror = () => {
      clearTimeout(timer)
      _wsConnected = false
      _stopHeartbeat()
      resolve(false)
    }

    ws.onclose = (event) => {
      clearTimeout(timer)
      _wsConnected = false
      _stopHeartbeat()
      if (gen === _wsGeneration) _ws = null
      
      // 通知前端连接已断开
      for (const handler of _eventHandlers) {
        handler({ event: 'disconnected', data: { code: event.code, reason: event.reason } })
      }
      
      // 自动重连（非手动关闭时）
      if (!_wsManuallyClosed && gen === _wsGeneration) {
        _scheduleReconnect()
      }
    }
  })
}

function _scheduleReconnect() {
  if (_wsReconnectTimer) return
  
  _wsReconnectAttempts++
  const delay = Math.min(
    _WS_RECONNECT_DELAY * Math.pow(2, _wsReconnectAttempts - 1),
    _WS_MAX_RECONNECT_DELAY
  )
  
  console.log(`[WS] ${delay}ms 后尝试第 ${_wsReconnectAttempts} 次重连`)
  
  _wsReconnectTimer = setTimeout(async () => {
    _wsReconnectTimer = null
    const success = await ensureWs()
    if (!success) {
      console.warn(`[WS] 第 ${_wsReconnectAttempts} 次重连失败`)
    } else {
      console.log(`[WS] 第 ${_wsReconnectAttempts} 次重连成功`)
    }
  }, delay)
}

export function onWsEvent(handler: (event: WsEvent) => void): () => void {
  _eventHandlers.push(handler)
  return () => {
    const idx = _eventHandlers.indexOf(handler)
    if (idx >= 0) _eventHandlers.splice(idx, 1)
  }
}

export function wsSend(data: SendableWsMessage | { type: 'ping' } | { type: 'login'; username: string }) {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify(data))
  }
}

function _flushPendingMessages() {
  if (_pendingMessages.length === 0) return
  console.log(`[WS] Flushing ${_pendingMessages.length} pending messages`)
  const pending = [..._pendingMessages]
  _pendingMessages.length = 0
  const now = Date.now()
  for (const p of pending) {
    if (now - p.createdAt <= _PENDING_TTL_MS) wsSend(p.payload)
  }
}

export interface ImageData {
  dataUrl: string
  name: string
  size: number
}

function _sendOrQueue(msg: SendableWsMessage) {
  if (!_ws || _ws.readyState !== WebSocket.OPEN) {
    if (!_isQueueableWsMessage(msg)) {
      console.warn(`[WS] Dropped non-queueable offline message: ${msg.type}`)
      return
    }
    const now = Date.now()
    msg.client_message_id = msg.client_message_id || `${msg.type}-${now}-${Math.random().toString(36).slice(2)}`
    _pendingMessages.push({ id: msg.client_message_id, createdAt: now, payload: msg })
    while (_pendingMessages.length > _PENDING_MAX) _pendingMessages.shift()
    console.log(`[WS] Queued message (offline), pending=${_pendingMessages.length}`)
    return
  }
  wsSend(msg)
}

export function wsChat(message: string, sessionId?: string, workspace?: string, images?: ImageData[]) {
  const chatMsg: ChatWsMessage = { type: 'chat', message, ..._usernameBody() }
  if (sessionId) chatMsg.session_id = sessionId
  if (workspace) chatMsg.workspace = workspace
  if (images && images.length > 0) chatMsg.images = images
  _sendOrQueue(chatMsg)
}

export function abortChat(sessionId?: string, workspace?: string) {
  const msg: AbortWsMessage = { type: 'abort', ..._usernameBody() }
  if (sessionId) msg.session_id = sessionId
  if (workspace) msg.workspace = workspace
  wsSend(msg)
}

export function startPlan(sessionId?: string, workspace?: string, initialGoal?: string) {
  const msg: PlanStartWsMessage = { type: 'plan.start', goal: initialGoal || '', ..._usernameBody() }
  if (sessionId) msg.session_id = sessionId
  if (workspace) msg.workspace = workspace
  _sendOrQueue(msg)
}

export function sendPlanMessage(message: string, sessionId?: string, workspace?: string, images?: ImageData[]) {
  const msg: PlanMessageWsMessage = { type: 'plan.message', message, ..._usernameBody() }
  if (sessionId) msg.session_id = sessionId
  if (workspace) msg.workspace = workspace
  if (images && images.length > 0) msg.images = images
  _sendOrQueue(msg)
}

export function selectPlanOption(sessionId: string, planId: string, optionId: string, workspace?: string) {
  const msg: PlanSelectOptionWsMessage = { type: 'plan.select_option', session_id: sessionId, plan_id: planId, option_id: optionId, ..._usernameBody() }
  if (workspace) msg.workspace = workspace
  _sendOrQueue(msg)
}

export function approvePlan(sessionId: string, planId: string, revision: number, workspace?: string) {
  const msg: PlanApproveWsMessage = { type: 'plan.approve', session_id: sessionId, plan_id: planId, revision, ..._usernameBody() }
  if (workspace) msg.workspace = workspace
  _sendOrQueue(msg)
}

export function applyPlanDecision(
  sessionId: string,
  planId: string,
  revision: number,
  stepId: string,
  decisionId: string,
  selectedOptionIds: string[],
  customValue: string,
  workspace?: string,
) {
  const msg: PlanApplyDecisionWsMessage = {
    type: 'plan.apply_decision',
    session_id: sessionId,
    plan_id: planId,
    revision,
    step_id: stepId,
    decision_id: decisionId,
    selected_option_ids: selectedOptionIds,
    custom_value: customValue,
    ..._usernameBody(),
  }
  if (workspace) msg.workspace = workspace
  _sendOrQueue(msg)
}

export function revisePlan(sessionId: string, planId: string, instruction: string, workspace?: string) {
  const msg: PlanReviseWsMessage = { type: 'plan.revise', session_id: sessionId, plan_id: planId, message: instruction, ..._usernameBody() }
  if (workspace) msg.workspace = workspace
  _sendOrQueue(msg)
}

export function cancelPlan(sessionId: string, planId?: string, workspace?: string) {
  const msg: PlanCancelWsMessage = { type: 'plan.cancel', session_id: sessionId, plan_id: planId || '', ..._usernameBody() }
  if (workspace) msg.workspace = workspace
  _sendOrQueue(msg)
}

export function closeWs() {
  _wsManuallyClosed = true
  
  // 停止心跳
  _stopHeartbeat()
  
  // 取消重连定时器
  if (_wsReconnectTimer) {
    clearTimeout(_wsReconnectTimer)
    _wsReconnectTimer = null
  }
  
  if (_ws) {
    const ws = _ws
    _ws = null
    _wsConnected = false
    _eventHandlers.length = 0
    _wsReconnectAttempts = 0
    try { ws.close(1000, 'page refresh') } catch {}
  }
}

// ── Config ──


// ── Session APIs ──

export async function createSession(workspace?: string): Promise<{ session_id: string }> {
  const body: SessionRequestBody = { ..._usernameBody() }
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return _json(resp)
}

export async function getSessions(workspace?: string): Promise<SessionsResponse> {
  const params = new URLSearchParams()
  params.set('username', _username())
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/sessions?${params.toString()}`)
  return _json(resp)
}

export async function deleteSession(sessionId: string, workspace?: string): Promise<DeleteSessionResponse> {
  const body: SessionIdRequestBody = { session_id: sessionId, ..._usernameBody() }
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/session', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return _json(resp)
}

export async function batchDeleteSessions(sessionIds: string[], workspace?: string): Promise<BatchDeleteSessionsResponse> {
  const body: BatchDeleteSessionsRequestBody = { session_ids: sessionIds, ..._usernameBody() }
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/sessions/batch_delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return _json(resp)
}

export async function renameSession(sessionId: string, name: string, workspace?: string): Promise<RenameSessionResponse> {
  const body: RenameSessionRequestBody = { session_id: sessionId, name, ..._usernameBody() }
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/session/rename', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return _json(resp)
}

export async function getHistory(sessionId: string, workspace?: string): Promise<HistoryResponse> {
  const params = new URLSearchParams()
  params.set('session_id', sessionId)
  params.set('username', _username())
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/chat/history?${params.toString()}`)
  return _json(resp)
}

// ── Workspace APIs ──

export interface RemovedWorkspaceInfo {
  name: string
  project_path: string
}

export async function getWorkspaces(): Promise<WorkspacesResponse> {
  const resp = await _fetch(`/api/workspaces?username=${encodeURIComponent(_username())}`)
  return _json(resp)
}

export async function createWorkspace(name: string, projectPath?: string): Promise<WorkspaceMutationResponse> {
  const body: CreateWorkspaceRequestBody = { name, ..._usernameBody() }
  if (projectPath) body.project_path = projectPath
  const resp = await _fetch('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return _json(resp)
}

export async function addWorkspace(path: string): Promise<WorkspaceMutationResponse> {
  const resp = await _fetch('/api/workspaces/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, ..._usernameBody() }),
  })
  return _json(resp)
}

export async function switchWorkspace(name: string): Promise<WorkspaceMutationResponse> {
  const resp = await _fetch('/api/workspaces/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, ..._usernameBody() }),
  })
  return _json(resp)
}

export async function removeWorkspace(name: string, deleteData: boolean = false): Promise<WorkspaceMutationResponse> {
  const resp = await _fetch(`/api/workspaces/${encodeURIComponent(name)}?delete_data=${deleteData}&username=${encodeURIComponent(_username())}`, {
    method: 'DELETE',
  })
  return _json(resp)
}

export async function listRemovedWorkspaces(): Promise<RemovedWorkspacesResponse> {
  const resp = await _fetch(`/api/workspaces/removed?username=${encodeURIComponent(_username())}`)
  return _json(resp)
}

export async function restoreWorkspace(name: string): Promise<WorkspaceMutationResponse> {
  const resp = await _fetch('/api/workspaces/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, ..._usernameBody() }),
  })
  return _json(resp)
}

export async function deleteRemovedWorkspace(name: string): Promise<WorkspaceMutationResponse> {
  const resp = await _fetch(`/api/workspaces/removed/${encodeURIComponent(name)}?username=${encodeURIComponent(_username())}`, {
    method: 'DELETE',
  })
  return _json(resp)
}

// ── Other APIs ──

export async function getModels(sessionId?: string, workspace?: string): Promise<ModelsResponse> {
  const params = new URLSearchParams()
  if (sessionId) params.set('session_id', sessionId)
  if (workspace) params.set('workspace', workspace)
  const u = _username()
  if (u) params.set('username', u)
  const query = params.toString()
  const resp = await _fetch(`/api/models${query ? '?' + query : ''}`)
  return _json(resp)
}

export async function switchModel(name: string, sessionId?: string, workspace?: string): Promise<SwitchModelResponse> {
  const body: SwitchModelRequestBody = { name, ..._usernameBody() }
  if (sessionId) body.session_id = sessionId
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/models/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return _json(resp)
}

export async function getConfig(sessionId?: string, workspace?: string): Promise<ConfigResponse> {
  const params = new URLSearchParams()
  if (sessionId) params.set('session_id', sessionId)
  const u = _username()
  if (u) params.set('username', u)
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/config?${params.toString()}`)
  return _json(resp)
}

export async function getSystemPrompt(workspace?: string): Promise<SystemPromptResponse> {
  const params = new URLSearchParams()
  const u = _username()
  if (u) params.set('username', u)
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/config/system-prompt?${params.toString()}`)
  return _json(resp)
}

export async function resetChat(sessionId?: string, workspace?: string): Promise<ResetChatResponse> {
  const body: ResetChatRequestBody = { ..._usernameBody() }
  if (sessionId) body.session_id = sessionId
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/chat/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return _json(resp)
}

export interface SkillInfo {
  name: string
  description: string
  tags: string
  tier: string
}

export async function getSkills(username?: string, workspace?: string): Promise<SkillsListResponse> {
  const params = new URLSearchParams()
  if (username) params.set('username', username)
  if (workspace) params.set('workspace', workspace)
  const qs = params.toString()
  const resp = await _fetch('/api/skills' + (qs ? '?' + qs : ''))
  return _json(resp)
}

export interface DeleteSkillResponse {
  ok: boolean
  message?: string
  error?: string
}

export async function deleteSkill(name: string, username?: string, workspace?: string, level?: string): Promise<DeleteSkillResponse> {
  const params = new URLSearchParams()
  if (username) params.set('username', username)
  if (workspace) params.set('workspace', workspace)
  if (level) params.set('level', level)
  const qs = params.toString()
  const resp = await _fetch('/api/skills/' + encodeURIComponent(name) + (qs ? '?' + qs : ''), { method: 'DELETE' })
  return _json(resp)
}

export async function getCommands(): Promise<CommandsResponse> {
  const resp = await _fetch('/api/commands')
  return _json(resp)
}

export type ToolParameterSchema = JsonObject

export interface ToolDefinition extends JsonObject {
  name?: string
  description?: string
  parameters?: ToolParameterSchema
}

export interface ToolsResponse {
  tools: ToolDefinition[]
  count: number
  chars: number
  tokens: number
  tool_names: string[]
}

export async function getTools(sessionId?: string, workspace?: string): Promise<ToolsResponse> {
  const params = new URLSearchParams()
  params.set('username', _username())
  if (sessionId) params.set('session_id', sessionId)
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/config/tools?${params.toString()}`)
  return _json(resp)
}

export async function listFiles(path: string, workspace: string): Promise<FileListResponse> {
  const params = new URLSearchParams()
  params.set('path', path)
  params.set('workspace', workspace)
  params.set('username', _username())
  const resp = await _fetch(`/api/files/list?${params.toString()}`)
  return _json(resp)
}

export async function readFile(path: string, workspace: string, offset = 0, limit = 200): Promise<FileReadResponse> {
  const params = new URLSearchParams()
  params.set('path', path)
  params.set('workspace', workspace)
  params.set('offset', String(offset))
  params.set('limit', String(limit))
  params.set('username', _username())
  const resp = await _fetch(`/api/files/read?${params.toString()}`)
  return _json(resp)
}

export async function searchFiles(query: string, path: string, workspace: string): Promise<FileSearchResponse> {
  const params = new URLSearchParams()
  params.set('query', query)
  params.set('path', path)
  params.set('workspace', workspace)
  params.set('username', _username())
  const resp = await _fetch(`/api/files/search?${params.toString()}`)
  return _json(resp)
}

export interface BrowseDir {
  name: string
  path: string
  has_children: boolean
}

export interface BrowseResponse {
  current: string
  parent: string
  dirs: BrowseDir[]
  error?: string
}

export async function browseDirs(path?: string): Promise<BrowseResponse> {
  const params = new URLSearchParams()
  if (path) params.set('path', path)
  const u = _username()
  if (u) params.set('username', u)
  const resp = await _fetch(`/api/files/browse?${params.toString()}`)
  return _json(resp)
}

export interface SearchResult {
  ts: string
  role: string
  content: string
}

export async function searchHistory(keyword: string, sessionId?: string, workspace?: string, dateFrom?: string, dateTo?: string, limit?: number): Promise<{ results: SearchResult[] }> {
  const params = new URLSearchParams()
  params.set('keyword', keyword)
  if (sessionId) params.set('session_id', sessionId)
  params.set('username', _username())
  if (workspace) params.set('workspace', workspace)
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)
  if (limit) params.set('limit', String(limit))
  const resp = await _fetch(`/api/chat/search?${params.toString()}`)
  return _json(resp)
}


// ── Settings APIs ──

export type ApiMode = 'openai' | 'anthropic'
export type McpServerType = 'stdio' | 'streamable_http' | 'sse'

export interface ThinkingSettings {
  enabled?: boolean
  budget_tokens?: number
  type?: string
}

export interface SettingsModelInfo {
  api_url: string
  api_mode: ApiMode | string
  model: string
  context_length: number
  temperature?: number | null
  max_tokens?: number | null
  top_p?: number | null
  reasoning_effort?: string | null
  thinking?: ThinkingSettings | null
}

export interface DisplaySettings {
  thinking_mode?: string
  tool_detail?: string
}

export interface CompactorSettings extends JsonObject {
  context_limit?: number
  keep_recent?: number
  keep_budget_ratio?: number
  early_compact_ratio?: number
  max_cached_summaries?: number
}

export interface RunnerSettings extends JsonObject {
  max_turns?: number
  context_usage_limit?: number
}

export interface PlanSettings extends JsonObject {
  approval?: boolean
}

export interface ToolSettings extends JsonObject {
  max_result_chars?: number
}

export interface WebSettings extends JsonObject {
  history_limit?: number
}

export interface LoggingSettings extends JsonObject {
  level?: string
}

export interface McpSettings extends JsonObject {
  enabled?: boolean
  servers?: Record<string, McpServerConfig & JsonObject>
}

export interface TeammateSettings extends JsonObject {
  max_teammates?: number
  max_turns?: number
  idle_timeout?: number
  max_history?: number
}

export interface TimeoutSettings extends JsonObject {
  llm?: number
  tool?: number
  mcp?: number
  teammate_idle?: number
}

export interface SettingsResponse {
  active_model: string
  models: Record<string, SettingsModelInfo>
  streaming: boolean
  thinking: ThinkingSettings
  display: DisplaySettings
  compactor: CompactorSettings
  timeouts: TimeoutSettings
  runner: RunnerSettings
  plan: PlanSettings
  tool: ToolSettings
  web: WebSettings
  logging: LoggingSettings
  mcp: McpSettings
  teammate: TeammateSettings
}

export interface ModelConfigUpdate extends Partial<Omit<SettingsModelInfo, 'api_url' | 'api_mode' | 'model'>> {
  name: string
  thinking?: ThinkingSettings | null
}

export interface SettingsUpdatePayload {
  active_model?: string
  model_config?: ModelConfigUpdate
  streaming?: boolean
  thinking?: ThinkingSettings
  display?: DisplaySettings
  runner?: RunnerSettings
  plan?: PlanSettings
  web?: WebSettings
  compactor?: CompactorSettings
  tool?: ToolSettings
  teammate?: TeammateSettings
  logging?: LoggingSettings
  mcp?: McpSettings
}

export interface UpdateSettingsResponse extends ApiMutationResponse {
  status?: 'ok'
  updated: string[]
}

export interface AddModelRequest {
  name: string
  api_key: string
  api_url: string
  api_mode: ApiMode
  model: string
  context_length?: number
  temperature?: number
  headers?: Record<string, string>
}

export interface AddModelResponse extends ApiMutationResponse {
  status?: 'ok'
  name?: string
}

export interface RemoveModelResponse extends ApiMutationResponse {
  status?: 'ok'
  removed?: string
  new_active?: string | null
}

export async function getSettings(): Promise<SettingsResponse> {
  const resp = await _fetch('/api/settings')
  return _json(resp)
}

export async function updateSettings(updates: SettingsUpdatePayload): Promise<UpdateSettingsResponse> {
  const resp = await _fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  return _json(resp)
}


export async function addModel(model: AddModelRequest): Promise<AddModelResponse> {
  const resp = await _fetch('/api/settings/add_model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(model),
  })
  return _json(resp)
}

export async function removeModel(name: string): Promise<RemoveModelResponse> {
  const resp = await _fetch('/api/settings/remove_model', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  return _json(resp)
}


// ── MCP APIs ──

export interface McpToolInfo {
  name: string
  description: string
}

export interface McpServerConfig {
  name?: string
  type: McpServerType
  command?: string
  args?: string[]
  url?: string
  headers?: Record<string, string>
}

export interface AddMcpServerRequest extends McpServerConfig {
  name: string
}

export interface AddMcpServerResponse extends ApiMutationResponse {
  status?: 'ok'
  name?: string
}

export interface RemoveMcpServerResponse extends ApiMutationResponse {
  status?: 'ok'
  removed?: string
}

export interface McpConnectedServer {
  name: string
  type: string
  tools: McpToolInfo[]
}

export interface McpConfiguredServer {
  name: string
  type: string
  disabled: boolean
}

export interface McpStatusResponse {
  enabled: boolean
  configured: McpConfiguredServer[]
  connected: McpConnectedServer[]
}

export async function getMcpStatus(): Promise<McpStatusResponse> {
  const resp = await _fetch('/api/mcp')
  return _json(resp)
}

export async function addMcpServer(server: AddMcpServerRequest): Promise<AddMcpServerResponse> {
  const resp = await _fetch('/api/settings/mcp/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(server),
  })
  return _json(resp)
}

export async function removeMcpServer(name: string): Promise<RemoveMcpServerResponse> {
  const resp = await _fetch(`/api/settings/mcp/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  return _json(resp)
}


export async function exportSession(sessionId: string, workspace?: string, limit?: number, includeThinking?: boolean, includeTools?: boolean): Promise<void> {
  const params = new URLSearchParams()
  params.set('session_id', sessionId)
  params.set('username', _username())
  if (workspace) params.set('workspace', workspace)
  if (limit && limit > 0) params.set('limit', String(limit))
  if (includeThinking) params.set('include_thinking', 'true')
  if (includeTools) params.set('include_tools', 'true')
  const resp = await _fetch(`/api/chat/export?${params.toString()}`, { timeout: 30000 })
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const cd = resp.headers.get('content-disposition') || ''
  const match = cd.match(/filename\*=UTF-8''(.+)/) || cd.match(/filename="?([^"]+)"?/)
  a.download = match ? decodeURIComponent(match[1]) : `${sessionId}.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export async function getTeamStatus(username: string, workspace: string): Promise<TeamStatusResponse> {
  return _withFallbackError(async () => {
    const params = new URLSearchParams()
    params.set('username', username)
    if (workspace) params.set('workspace', workspace)
    const resp = await _fetch(`/api/team/status?${params.toString()}`)
    return _json(resp)
  }, '获取 Team 状态失败')
}

export async function getBlackboard(username: string, workspace: string): Promise<BlackboardResponse> {
  return _withFallbackError(async () => {
    const params = new URLSearchParams()
    params.set('username', username)
    if (workspace) params.set('workspace', workspace)
    const resp = await _fetch(`/api/team/blackboard?${params.toString()}`)
    return _json(resp)
  }, '获取黑板失败')
}

export async function dismissTeammate(username: string, workspace: string, name: string): Promise<TeamMutationResponse> {
  return _withFallbackError(async () => {
    const resp = await _fetch('/api/team/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, workspace, name }),
    })
    return _json(resp)
  }, '解散队友失败')
}

export async function clearBlackboard(username: string, workspace: string): Promise<TeamMutationResponse> {
  return _withFallbackError(async () => {
    const resp = await _fetch('/api/team/blackboard/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, workspace }),
    })
    return _json(resp)
  }, '清空黑板失败')
}

export async function getTodos(sessionId: string, workspace?: string): Promise<TodosResponse> {
  const params = new URLSearchParams()
  params.set('username', _username())
  params.set('session_id', sessionId)
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/todos?${params.toString()}`)
  return _json(resp)
}
