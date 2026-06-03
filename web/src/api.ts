const USERNAME_KEY = 'mini-ai-username'
const _FETCH_TIMEOUT = 8000


const _origFetch = window.fetch.bind(window)

async function _fetch(url: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), _FETCH_TIMEOUT)
  try {
    const resp = await _origFetch(url, { ...init, signal: controller.signal })
    return resp
  } finally {
    clearTimeout(timer)
  }
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

export interface WsEvent {
  event: string
  data: any
}

export interface ModelInfo {
  name: string
  model: string
}

export interface ModelsResponse {
  active: string
  active_name: string
  models: ModelInfo[]
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

export interface HistoryMessage {
  tool_calls?: any[]
  role: string
  content?: string
  images?: ImageData[]
  thinking?: any
  timestamp?: string
}

export interface HistoryResponse {
  session_id: string
  history: HistoryMessage[]
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

function _username(): string {
  return getUsername() || 'default'
}

function _usernameBody(): object {
  const u = getUsername()
  return u ? { username: u } : {}
}

// ── WebSocket ──

let _ws: WebSocket | null = null
let _wsConnected = false
const _eventHandlers: ((event: WsEvent) => void)[] = []

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

function _wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/api/chat/ws`
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
      
      // 重置重连计数
      _wsReconnectAttempts = 0
      
      const u = getUsername()
      if (u) ws.send(JSON.stringify({ type: 'login', username: u }))
      
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
        const evt = JSON.parse(e.data)
        for (const handler of _eventHandlers) {
          handler(evt)
        }
      } catch {}
    }

    ws.onerror = () => {
      clearTimeout(timer)
      _wsConnected = false
      resolve(false)
    }

    ws.onclose = (event) => {
      clearTimeout(timer)
      _wsConnected = false
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

export function wsSend(data: object) {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify(data))
  }
}

export interface ImageData {
  dataUrl: string
  name: string
  size: number
}

export function wsChat(message: string, sessionId?: string, workspace?: string, planMode?: boolean, images?: ImageData[]) {
  const chatMsg: any = { type: 'chat', message, ..._usernameBody() }
  if (sessionId) chatMsg.session_id = sessionId
  if (workspace) chatMsg.workspace = workspace
  if (planMode) chatMsg.plan_mode = true
  if (images && images.length > 0) chatMsg.images = images
  wsSend(chatMsg)
}

export function abortChat(sessionId?: string, workspace?: string) {
  const msg: any = { type: 'abort', ..._usernameBody() }
  if (sessionId) msg.session_id = sessionId
  if (workspace) msg.workspace = workspace
  wsSend(msg)
}

export function sendPlan(sessionId?: string) {
  const msg: any = { type: 'plan', ..._usernameBody() }
  if (sessionId) msg.session_id = sessionId
  wsSend(msg)
}

export function sendAct(sessionId?: string) {
  const msg: any = { type: 'act', ..._usernameBody() }
  if (sessionId) msg.session_id = sessionId
  wsSend(msg)
}

export function closeWs() {
  _wsManuallyClosed = true
  
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
  const body: any = { ..._usernameBody() }
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function getSessions(workspace?: string): Promise<SessionsResponse> {
  const params = new URLSearchParams()
  params.set('username', _username())
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/sessions?${params.toString()}`)
  return resp.json()
}

export async function deleteSession(sessionId: string, workspace?: string): Promise<any> {
  const body: any = { session_id: sessionId, ..._usernameBody() }
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/session', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function batchDeleteSessions(sessionIds: string[], workspace?: string): Promise<any> {
  const body: any = { session_ids: sessionIds, ..._usernameBody() }
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/sessions/batch_delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function renameSession(sessionId: string, name: string, workspace?: string): Promise<any> {
  const body: any = { session_id: sessionId, name, ..._usernameBody() }
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/session/rename', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function getHistory(sessionId: string, workspace?: string): Promise<HistoryResponse> {
  const params = new URLSearchParams()
  params.set('session_id', sessionId)
  params.set('username', _username())
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/chat/history?${params.toString()}`)
  return resp.json()
}

// ── Workspace APIs ──

export interface RemovedWorkspaceInfo {
  name: string
  project_path: string
}

export async function getWorkspaces(): Promise<WorkspacesResponse> {
  const resp = await _fetch(`/api/workspaces?username=${encodeURIComponent(_username())}`)
  return resp.json()
}

export async function createWorkspace(name: string, projectPath?: string): Promise<any> {
  const body: any = { name, ..._usernameBody() }
  if (projectPath) body.project_path = projectPath
  const resp = await _fetch('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function addWorkspace(path: string): Promise<any> {
  const resp = await _fetch('/api/workspaces/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, ..._usernameBody() }),
  })
  return resp.json()
}

export async function switchWorkspace(name: string): Promise<any> {
  const resp = await _fetch('/api/workspaces/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, ..._usernameBody() }),
  })
  return resp.json()
}

export async function removeWorkspace(name: string, deleteData: boolean = false): Promise<any> {
  const resp = await _fetch(`/api/workspaces/${encodeURIComponent(name)}?delete_data=${deleteData}&username=${encodeURIComponent(_username())}`, {
    method: 'DELETE',
  })
  return resp.json()
}

export async function listRemovedWorkspaces(): Promise<{ removed: RemovedWorkspaceInfo[] }> {
  const resp = await _fetch(`/api/workspaces/removed?username=${encodeURIComponent(_username())}`)
  return resp.json()
}

export async function restoreWorkspace(name: string): Promise<any> {
  const resp = await _fetch('/api/workspaces/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, ..._usernameBody() }),
  })
  return resp.json()
}

export async function deleteRemovedWorkspace(name: string): Promise<any> {
  const resp = await _fetch(`/api/workspaces/removed/${encodeURIComponent(name)}?username=${encodeURIComponent(_username())}`, {
    method: 'DELETE',
  })
  return resp.json()
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
  return resp.json()
}

export async function switchModel(name: string, sessionId?: string, workspace?: string): Promise<any> {
  const body: any = { name, ..._usernameBody() }
  if (sessionId) body.session_id = sessionId
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/models/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function getConfig(sessionId?: string, workspace?: string): Promise<ConfigResponse> {
  const params = new URLSearchParams()
  if (sessionId) params.set('session_id', sessionId)
  const u = _username()
  if (u) params.set('username', u)
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/config?${params.toString()}`)
  return resp.json()
}

export async function getSystemPrompt(workspace?: string): Promise<{system_prompt: string, chars: number, tokens: number}> {
  const params = new URLSearchParams()
  const u = _username()
  if (u) params.set('username', u)
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/config/system-prompt?${params.toString()}`)
  return resp.json()
}

export async function resetChat(sessionId?: string, workspace?: string): Promise<any> {
  const body: any = { ..._usernameBody() }
  if (sessionId) body.session_id = sessionId
  if (workspace) body.workspace = workspace
  const resp = await _fetch('/api/chat/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export interface SkillInfo {
  name: string
  description: string
  tags: string
  tier: string
}

export async function getSkills(username?: string, workspace?: string): Promise<{skills: SkillInfo[]}> {
  const params = new URLSearchParams()
  if (username) params.set('username', username)
  if (workspace) params.set('workspace', workspace)
  const qs = params.toString()
  const resp = await _fetch('/api/skills' + (qs ? '?' + qs : ''))
  return resp.json()
}

export async function deleteSkill(name: string, username?: string, workspace?: string, level?: string): Promise<any> {
  const params = new URLSearchParams()
  if (username) params.set('username', username)
  if (workspace) params.set('workspace', workspace)
  if (level) params.set('level', level)
  const qs = params.toString()
  const resp = await _fetch('/api/skills/' + encodeURIComponent(name) + (qs ? '?' + qs : ''), { method: 'DELETE' })
  return resp.json()
}

export async function getCommands(): Promise<CommandsResponse> {
  const resp = await _fetch('/api/commands')
  return resp.json()
}

export interface ToolsResponse {
  tools: any[]
  count: number
  chars: number
  tokens: number
  tool_names: string[]
}

export async function getTools(): Promise<ToolsResponse> {
  const resp = await _fetch('/api/config/tools')
  return resp.json()
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
  return resp.json()
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
  return resp.json()
}


// ── Settings APIs ──

export interface SettingsResponse {
  active_model: string
  models: Record<string, {
    api_url: string
    api_mode: string
    model: string
    context_length: number
    temperature?: number | null
    max_tokens?: number | null
    top_p?: number | null
    reasoning_effort?: string | null
    thinking?: { enabled?: boolean; budget_tokens?: number; type?: string } | null
  }>
  streaming: boolean
  thinking: { enabled: boolean; budget_tokens: number; type: string }
  display: { thinking_mode: string; tool_detail: string }
  compactor: Record<string, any>
  timeouts: Record<string, any>
  runner: Record<string, any>
  plan: Record<string, any>
  tool: Record<string, any>
  web: Record<string, any>
  logging: Record<string, any>
  mcp: { enabled: boolean; servers?: Record<string, any> }
  teammate: Record<string, any>
}

export async function getSettings(): Promise<SettingsResponse> {
  const resp = await _fetch('/api/settings')
  return resp.json()
}

export async function updateSettings(updates: Record<string, any>): Promise<any> {
  const resp = await _fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  return resp.json()
}


export async function addModel(model: {
  name: string
  api_key: string
  api_url: string
  api_mode: 'openai' | 'anthropic'
  model: string
  context_length?: number
  temperature?: number
  headers?: Record<string, string>
}): Promise<any> {
  const resp = await _fetch('/api/settings/add_model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(model),
  })
  return resp.json()
}

export async function removeModel(name: string): Promise<any> {
  const resp = await _fetch('/api/settings/remove_model', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  return resp.json()
}


// ── MCP APIs ──

export interface McpToolInfo {
  name: string
  description: string
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
  return resp.json()
}

export async function addMcpServer(server: {
  name: string
  type: 'stdio' | 'streamable_http' | 'sse'
  command?: string
  args?: string[]
  url?: string
  headers?: Record<string, string>
}): Promise<any> {
  const resp = await _fetch('/api/settings/mcp/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(server),
  })
  return resp.json()
}

export async function removeMcpServer(name: string): Promise<any> {
  const resp = await _fetch(`/api/settings/mcp/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  return resp.json()
}


export async function exportSession(sessionId: string, workspace?: string, limit?: number, includeThinking?: boolean, includeTools?: boolean): Promise<void> {
  const params = new URLSearchParams()
  params.set('session_id', sessionId)
  params.set('username', _username())
  if (workspace) params.set('workspace', workspace)
  if (limit && limit > 0) params.set('limit', String(limit))
  if (includeThinking) params.set('include_thinking', 'true')
  if (includeTools) params.set('include_tools', 'true')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 30000)
  let resp: Response
  try {
    resp = await _origFetch(`/api/chat/export?${params.toString()}`, { signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}))
    throw new Error(data.error || '导出失败')
  }
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

export async function getTeamStatus(username: string, workspace: string): Promise<any> {
  const params = new URLSearchParams()
  params.set('username', username)
  if (workspace) params.set('workspace', workspace)
  const resp = await _origFetch(`/api/team/status?${params.toString()}`)
  if (!resp.ok) throw new Error('获取 Team 状态失败')
  return resp.json()
}

export async function getBlackboard(username: string, workspace: string): Promise<any> {
  const params = new URLSearchParams()
  params.set('username', username)
  if (workspace) params.set('workspace', workspace)
  const resp = await _origFetch(`/api/team/blackboard?${params.toString()}`)
  if (!resp.ok) throw new Error('获取黑板失败')
  return resp.json()
}

export async function dismissTeammate(username: string, workspace: string, name: string): Promise<any> {
  const resp = await _origFetch('/api/team/dismiss', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, workspace, name }),
  })
  if (!resp.ok) throw new Error('解散队友失败')
  return resp.json()
}

export async function clearBlackboard(username: string, workspace: string): Promise<any> {
  const resp = await _origFetch('/api/team/blackboard/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, workspace }),
  })
  if (!resp.ok) throw new Error('清空黑板失败')
  return resp.json()
}

export async function getTodos(sessionId: string, workspace?: string): Promise<{ todos: any[] }> {
  const params = new URLSearchParams()
  params.set('username', _username())
  params.set('session_id', sessionId)
  if (workspace) params.set('workspace', workspace)
  const resp = await _fetch(`/api/todos?${params.toString()}`)
  return resp.json()
}
