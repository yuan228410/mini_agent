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

export async function* streamChat(message: string, sessionId?: string): AsyncGenerator<SSEEvent> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
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

export async function createSession(): Promise<{ session_id: string }> {
  const resp = await fetch('/api/session', { method: 'POST' })
  return resp.json()
}

export async function getHistory(sessionId: string): Promise<HistoryResponse> {
  const resp = await fetch(`/api/chat/history?session_id=${encodeURIComponent(sessionId)}`)
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
  const url = sessionId
    ? `/api/config?session_id=${encodeURIComponent(sessionId)}`
    : '/api/config'
  const resp = await fetch(url)
  return resp.json()
}

export async function resetChat(sessionId?: string): Promise<any> {
  const resp = await fetch('/api/chat/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
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
