<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCallBlock from './ToolCallBlock.vue'

marked.use({
  breaks: true,
  gfm: true,
})

const props = defineProps<{
  message: {
    role: string
    content: string
    thinking?: { chars: number; elapsed: number; content: string }
    tools?: { name: string; args: string; result: string; elapsed: number }[]
    streaming?: boolean
    timestamp?: string
  }
}>()

const renderedContent = computed(() => {
  if (!props.message.content) return ''
  let html = marked.parse(props.message.content) as string
  if (props.message.streaming) return html
  html = html.replace(/<pre><code(?: class="language-(\w+)")?>([\s\S]*?)<\/code><\/pre>/g,
    (_match: string, lang: string, code: string) => {
      const decoded = code.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
      const highlighted = lang && hljs.getLanguage(lang)
        ? hljs.highlight(decoded, { language: lang }).value
        : hljs.highlightAuto(decoded).value
      return `<pre><code class="hljs language-${lang || ''}">${highlighted}</code></pre>`
    }
  )
  return html
})

const isUser = computed(() => props.message.role === 'user')
const label = computed(() => props.message.role === 'user' ? 'You' : 'mini_ai')
const timeLabel = computed(() => {
  const ts = props.message.timestamp
  if (!ts) return ''
  return ts.length > 2 ? ts.slice(2).replace('T', ' ') : ts
})
</script>

<template>
  <div class="message" :class="{ 'message--user': isUser, 'message--assistant': !isUser }">
    <div class="message-label">{{ label }}<span v-if="timeLabel" class="message-time">{{ timeLabel }}</span></div>
    <ThinkingBlock v-if="message.thinking" :thinking="message.thinking" />
    <ToolCallBlock v-for="(tool, i) in message.tools" :key="i" :tool="tool" />
    <div v-if="message.content" class="message-body" v-html="renderedContent"></div>
    <span v-if="message.streaming" class="streaming-cursor"></span>
  </div>
</template>

<style scoped>
.message {
  padding: 1.2rem 0;
  border-bottom: 0.5px solid var(--border-light);
  animation: fadeInUp 0.3s ease forwards;
}

.message:last-child {
  border-bottom: none;
}

.message--user {
  text-align: right;
}

.message--user .message-time {
  font-weight: 400;
  font-size: 0.7rem;
  margin-left: 0.5rem;
  color: var(--fg-muted);
  opacity: 0.6;
}

.message-label {
  justify-content: flex-end;
}

.message-time {
  font-weight: 400;
  font-size: 0.7rem;
  margin-left: 0.5rem;
  color: var(--fg-muted);
  opacity: 0.6;
}

.message-label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.75rem;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--fg-dim);
  margin-bottom: 0.4rem;
}

.message-body {
  font-size: 1rem;
  line-height: 1.75;
  color: var(--fg);
  word-break: break-word;
}

.message-body :deep(h1),
.message-body :deep(h2),
.message-body :deep(h3),
.message-body :deep(h4) {
  font-family: 'Playfair Display', serif;
  margin-top: 1.2em;
  margin-bottom: 0.5em;
  color: var(--fg);
}

.message-body :deep(h1) { font-size: 1.5rem; }
.message-body :deep(h2) { font-size: 1.25rem; }
.message-body :deep(h3) { font-size: 1.1rem; }

.message-body :deep(p) {
  margin-bottom: 0.8em;
}

.message-body :deep(p:last-child) {
  margin-bottom: 0;
}

.message-body :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.88em;
  padding: 0.15em 0.4em;
  background: var(--bg-code);
  border-radius: 3px;
}

.message-body :deep(pre) {
  margin: 1em 0;
  padding: 1rem 1.2rem;
  background: var(--bg-code);
  border-radius: 6px;
  overflow-x: auto;
  box-shadow: 0 1px 3px var(--shadow-code);
}

.message-body :deep(pre code) {
  padding: 0;
  background: none;
  font-size: 0.85rem;
  line-height: 1.6;
}

.message-body :deep(blockquote) {
  margin: 0.8em 0;
  padding-left: 1rem;
  border-left: 2px solid var(--accent);
  color: var(--fg-muted);
}

.message-body :deep(ul),
.message-body :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.5em;
}

.message-body :deep(li) {
  margin-bottom: 0.3em;
}

.message-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.8em 0;
  font-size: 0.9rem;
}

.message-body :deep(th),
.message-body :deep(td) {
  padding: 0.5em 0.8em;
  border-bottom: 0.5px solid var(--border);
  text-align: left;
}

.message-body :deep(th) {
  font-weight: 600;
  color: var(--fg-muted);
}

.message-body :deep(a) {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.2s ease;
}

.message-body :deep(a:hover) {
  border-bottom-color: var(--accent);
}

.message-body :deep(hr) {
  border: none;
  border-top: 0.5px solid var(--border);
  margin: 1.5em 0;
}

.message--user .message-body {
  color: var(--fg);
}
</style>
