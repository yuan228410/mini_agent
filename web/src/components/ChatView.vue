<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { streamChat, getConfig, createSession, getHistory, resetChat, type HistoryMessage } from '../api'
import MessageItem from './MessageItem.vue'
import InputBar from './InputBar.vue'

interface Message {
  role: 'user' | 'assistant'
  content: string
  thinking?: { chars: number; elapsed: number; content: string }
  tools?: { name: string; args: string; result: string; elapsed: number }[]
  streaming?: boolean
}

const SESSION_KEY = 'mini-ai-session-id'
const messages = ref<Message[]>([])
const isStreaming = ref(false)
const chatContainer = ref<HTMLElement>()
const sessionId = ref('')
const emit = defineEmits(['config-update'])

onMounted(async () => {
  await initSession()
  await fetchConfig()
})

async function initSession() {
  const stored = localStorage.getItem(SESSION_KEY)
  if (stored) {
    sessionId.value = stored
    await restoreHistory()
  } else {
    await newSession()
  }
}

async function newSession() {
  try {
    const resp = await createSession()
    sessionId.value = resp.session_id
    localStorage.setItem(SESSION_KEY, resp.session_id)
  } catch {
    sessionId.value = 'default'
  }
}

async function restoreHistory() {
  try {
    const resp = await getHistory(sessionId.value)
    messages.value = resp.history
      .filter((m: HistoryMessage) => m.role !== 'system')
      .map((m: HistoryMessage) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content || '',
        thinking: m.thinking,
      }))
  } catch {
    messages.value = []
  }
}

async function fetchConfig() {
  try {
    const c = await getConfig(sessionId.value)
    emit('config-update', c)
  } catch {}
}

async function sendMessage(text: string) {
  if (!text.trim() || isStreaming.value) return

  // Handle / commands that don't need LLM
  if (text.startsWith('/clear')) {
    await resetChat(sessionId.value)
    messages.value = []
    await fetchConfig()
    return
  }

  isStreaming.value = true

  messages.value.push({ role: 'user', content: text })
  const assistantMsg: Message = { role: 'assistant', content: '', tools: [], streaming: true }
  messages.value.push(assistantMsg)

  await nextTick()
  scrollToBottom()

  let currentContent = ''
  let currentThinking = ''
  let thinkingChars = 0
  let thinkingElapsed = 0

  try {
    for await (const event of streamChat(text, sessionId.value)) {
      const msg = messages.value[messages.value.length - 1]

      switch (event.event) {
        case 'thinking_start':
          currentThinking = ''
          break
        case 'thinking':
          currentThinking += event.data.content || ''
          break
        case 'thinking_end':
          thinkingChars = event.data.chars || currentThinking.length
          thinkingElapsed = event.data.elapsed || 0
          msg.thinking = { chars: thinkingChars, elapsed: thinkingElapsed, content: currentThinking }
          currentThinking = ''
          break
        case 'text':
          currentContent += event.data.content || ''
          msg.content = currentContent
          break
        case 'tool_start':
          if (!msg.tools) msg.tools = []
          msg.tools.push({
            name: event.data.name || '?',
            args: event.data.args || '',
            result: '...',
            elapsed: 0,
          })
          break
        case 'tool_result':
          if (msg.tools && msg.tools.length > 0) {
            const last = msg.tools[msg.tools.length - 1]
            last.result = event.data.result || ''
            last.elapsed = event.data.elapsed || 0
          }
          break
        case 'done':
          msg.streaming = false
          if (event.data.session_id) {
            sessionId.value = event.data.session_id
            localStorage.setItem(SESSION_KEY, event.data.session_id)
          }
          break
        case 'error':
          msg.content += `\n\n⚠ 错误: ${event.data.error || '未知错误'}`
          msg.streaming = false
          break
      }

      await nextTick()
      scrollToBottom()
    }
  } catch (e: any) {
    const msg = messages.value[messages.value.length - 1]
    msg.content += `\n\n⚠ 连接错误: ${e.message}`
    msg.streaming = false
  }

  isStreaming.value = false
  await fetchConfig()
}

function useSkill(name: string) {
  sendMessage(`/skill ${name}`)
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

defineExpose({ useSkill })
</script>

<template>
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
  <InputBar :disabled="isStreaming" @send="sendMessage" />
</template>

<style scoped>
.chat-view {
  flex: 1;
  overflow-y: auto;
  padding: 2rem 0;
}

.messages {
  max-width: 720px;
  margin: 0 auto;
  padding: 0 1.5rem;
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
  height: 64px;
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
</style>
