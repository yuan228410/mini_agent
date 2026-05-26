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
  system_prompt_chars: number
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
  thinking?: any
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
  status: 'idle' | 'generating'
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

    ws.onclose = () => {
      clearTimeout(timer)
      _wsConnected = false
      if (gen === _wsGeneration) _ws = null
    }
  })
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

export function wsChat(message: string, sessionId?: string, workspace?: string) {
  const chatMsg: any = { type: 'chat', message, ..._usernameBody() }
  if (sessionId) chatMsg.session_id = sessionId
  if (workspace) chatMsg.workspace = workspace
  wsSend(chatMsg)
}

export function abortChat(sessionId?: string) {
  const msg: any = { type: 'abort', ..._usernameBody() }
  if (sessionId) msg.session_id = sessionId
  wsSend(msg)
}

export function closeWs() {
  if (_ws) {
    const ws = _ws
    _ws = null
    _wsConnected = false
    _eventHandlers.length = 0
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

export async function deleteSession(sessionId: string): Promise<any> {
  const resp = await _fetch('/api/session', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, ..._usernameBody() }),
  })
  return resp.json()
}

export async function renameSession(sessionId: string, name: string): Promise<any> {
  const resp = await _fetch('/api/session/rename', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, name, ..._usernameBody() }),
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

// ── Other APIs ──

export async function getModels(): Promise<ModelsResponse> {
  const resp = await _fetch('/api/models')
  return resp.json()
}

export async function switchModel(name: string, sessionId?: string): Promise<any> {
  const body: any = { name, ..._usernameBody() }
  if (sessionId) body.session_id = sessionId
  const resp = await _fetch('/api/models/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function getConfig(sessionId?: string): Promise<ConfigResponse> {
  const params = new URLSearchParams()
  if (sessionId) params.set('session_id', sessionId)
  const u = _username()
  if (u) params.set('username', u)
  const resp = await _fetch(`/api/config?${params.toString()}`)
  return resp.json()
}

export async function resetChat(sessionId?: string): Promise<any> {
  const body: any = { ..._usernameBody() }
  if (sessionId) body.session_id = sessionId
  const resp = await _fetch('/api/chat/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function getSkills(): Promise<any> {
  const resp = await _fetch('/api/skills')
  return resp.json()
}

export async function getCommands(): Promise<CommandsResponse> {
  const resp = await _fetch('/api/commands')
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
