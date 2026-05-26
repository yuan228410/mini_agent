const USERNAME_KEY = 'mini-ai-username'

export function getUsername(): string {
  return localStorage.getItem(USERNAME_KEY) || ''
}

export function setUsername(name: string) {
  localStorage.setItem(USERNAME_KEY, name)
}

export function hasUsername(): boolean {
  return !!localStorage.getItem(USERNAME_KEY)
}

export interface SSEEvent {
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
  transport: string
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
  message_count: number
  preview: string
}

export interface SessionsResponse {
  sessions: SessionInfo[]
}

function _usernameBody(): object {
  const u = getUsername()
  return u ? { username: u } : {}
}

// ── SSE (回退) ──

export async function* streamChat(message: string, sessionId?: string): AsyncGenerator<SSEEvent> {
  const body: any = { message, ..._usernameBody() }
  if (sessionId) body.session_id = sessionId

  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (!resp.ok || !resp.body) {
    throw new Error(`Chat request failed: ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    let currentEvent = ''
    let currentData = ''

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7)
      } else if (line.startsWith('data: ')) {
        currentData = line.slice(6)
      } else if (line === '' && currentEvent) {
        try {
          yield { event: currentEvent, data: JSON.parse(currentData) }
        } catch {
          yield { event: currentEvent, data: {} }
        }
        currentEvent = ''
        currentData = ''
      }
    }
  }
}

// ── WebSocket (持久连接) ──

let _ws: WebSocket | null = null
let _wsConnected = false
const _eventHandlers: ((event: SSEEvent) => void)[] = []

function _wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/api/chat/ws`
}

export async function ensureWs(): Promise<boolean> {
  if (_ws && _wsConnected && _ws.readyState === WebSocket.OPEN) return true

  return new Promise<boolean>((resolve) => {
    const ws = new WebSocket(_wsUrl())
    _ws = ws
    _wsConnected = false

    ws.onopen = () => {
      _wsConnected = true
      resolve(true)
    }

    ws.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data)
        for (const handler of _eventHandlers) {
          handler(evt)
        }
      } catch {}
    }

    ws.onerror = () => {
      _wsConnected = false
      resolve(false)
    }

    ws.onclose = () => {
      _wsConnected = false
      _ws = null
    }
  })
}

export function onWsEvent(handler: (event: SSEEvent) => void): () => void {
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

export function wsChat(message: string, sessionId?: string) {
  const chatMsg: any = { type: 'chat', message, ..._usernameBody() }
  if (sessionId) chatMsg.session_id = sessionId
  wsSend(chatMsg)
}

export function abortChat() {
  wsSend({ type: 'abort' })
}

export function closeWs() {
  if (_ws) {
    _ws.close()
    _ws = null
    _wsConnected = false
  }
}

// ── Config ──

let _transport: string | null = null

export async function getTransport(): Promise<string> {
  if (_transport) return _transport
  try {
    const c = await getConfig()
    _transport = c.transport || 'ws'
  } catch {
    _transport = 'ws'
  }
  return _transport
}

// ── REST APIs ──

export async function createSession(): Promise<{ session_id: string }> {
  const resp = await fetch('/api/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(_usernameBody()),
  })
  return resp.json()
}

export async function getSessions(): Promise<SessionsResponse> {
  const u = getUsername()
  const resp = await fetch(`/api/sessions?username=${encodeURIComponent(u || 'default')}`)
  return resp.json()
}

export async function getHistory(sessionId: string): Promise<HistoryResponse> {
  const u = getUsername()
  const resp = await fetch(`/api/chat/history?session_id=${encodeURIComponent(sessionId)}&username=${encodeURIComponent(u || 'default')}`)
  return resp.json()
}

export async function getModels(): Promise<ModelsResponse> {
  const resp = await fetch('/api/models')
  return resp.json()
}

export async function switchModel(name: string): Promise<any> {
  const resp = await fetch('/api/models/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  return resp.json()
}

export async function getConfig(sessionId?: string): Promise<ConfigResponse> {
  const u = getUsername()
  const params = new URLSearchParams()
  if (sessionId) params.set('session_id', sessionId)
  if (u) params.set('username', u)
  const resp = await fetch(`/api/config?${params.toString()}`)
  return resp.json()
}

export async function resetChat(sessionId?: string): Promise<any> {
  const body: any = { ..._usernameBody() }
  if (sessionId) body.session_id = sessionId
  const resp = await fetch('/api/chat/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function getSkills(): Promise<any> {
  const resp = await fetch('/api/skills')
  return resp.json()
}

export async function getCommands(): Promise<CommandsResponse> {
  const resp = await fetch('/api/commands')
  return resp.json()
}
