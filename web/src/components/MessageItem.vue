<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCallBlock from './ToolCallBlock.vue'
import type { ImageData } from '../api'

marked.use({
  breaks: true,
  gfm: true,
})

const props = defineProps<{
  message: {
    role: string
    content: string
    images?: ImageData[]
    thinking?: { chars: number; elapsed: number; content: string }
    tools?: { name: string; args: string; result: string; elapsed: number }[]
    streaming?: boolean
    timestamp?: string
    teammate?: string
    teammateColor?: string
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
const isTeammate = computed(() => !!props.message.teammate)
const label = computed(() => {
  if (props.message.role === 'user') return 'You'
  if (props.message.teammate) {
    const tm = props.message.teammate
    if (tm.startsWith('sub:')) return `📦 ${tm.slice(4)}`
    if (tm.startsWith('wf:')) return `🔀 ${tm.slice(3)}`
    return `🤖 ${tm}`
  }
  return 'mini_ai'
})
const timeLabel = computed(() => {
  const ts = props.message.timestamp
  if (!ts) return ''
  return ts.length > 2 ? ts.slice(2).replace('T', ' ') : ts
})

function openImage(dataUrl: string) {
  // 在新窗口打开图片
  const win = window.open()
  if (win) {
    win.document.write(`<html><head><title>图片预览</title></head><body style="margin:0;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#1a1a1a;"><img src="${dataUrl}" style="max-width:100%;max-height:100vh;object-fit:contain;"></body></html>`)
    win.document.close()
  }
}
</script>

<template>
  <div class="message" :class="{
      'message--user': isUser,
      'message--assistant': !isUser,
      'message--teammate': isTeammate,
    }"
    :style="isTeammate ? { borderLeftColor: message.teammateColor || '#888' } : {}"
  >
    <div class="message-row">
      <span class="message-label" :style="isTeammate ? { color: message.teammateColor || '#888' } : {}">{{ label }}</span>
      <span v-if="timeLabel" class="message-time">{{ timeLabel }}</span>
    </div>
    <ThinkingBlock v-if="message.thinking" :thinking="message.thinking" />
    <ToolCallBlock v-for="(tool, i) in message.tools" :key="i" :tool="tool" />
    
    <!-- 图片显示 -->
    <div v-if="message.images && message.images.length > 0" class="message-images">
      <div v-for="(img, i) in message.images" :key="i" class="message-image">
        <img :src="img.dataUrl" :alt="img.name" @click="openImage(img.dataUrl)" />
      </div>
    </div>
    
    <div v-if="message.content" class="message-body" v-html="renderedContent"></div>
    <span v-if="message.streaming" class="streaming-cursor"></span>
  </div>
</template>

<style scoped>
.message {
  padding: 0.6rem 0.8rem;
  margin: 0.4rem 0;
  border-bottom: none;
  animation: fadeInUp 0.35s ease forwards;
  border-radius: 10px;
}


.message:last-child {
  border-bottom: none;
}

.message--user {
  text-align: right;
  background: var(--accent-soft);
  margin-left: 8%;
}

.message--user .message-row {
  color: var(--accent);
  font-weight: 600;
}

.message--assistant {
  margin-right: 8%;
}

.message--assistant .message-row {
  color: #5b8a5e;
  font-weight: 500;
}

.message--teammate {
  border-left: 3px solid #888;
  padding-left: 0.6rem;
  margin-left: 0;
  background: var(--bg-tool);
  border-radius: 0 8px 8px 0;
}

.message--user .message-time {
  color: #c97a22;
  opacity: 0.7;
}

.message--assistant .message-time {
  color: #6a9e6d;
  opacity: 0.8;
}

.message-row {
  display: inline;
  font-size: 0.65rem;
  line-height: 1;
  color: var(--fg-dim);
}

.message-label {
  font-weight: 500;
}

.message-time {
  font-weight: 400;
  margin-left: 0.3rem;
  color: var(--fg-muted);
  opacity: 0.6;
}

.message-body {
  margin-top: 0;
}

.message-body {
  font-size: 1rem;
  line-height: 1.65;
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
  margin-bottom: 0.4em;
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
  border-radius: 8px;
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
  text-align: left;
}

.message-images {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 0.5rem;
}

.message-image {
  max-width: 200px;
  max-height: 200px;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.message-image:hover {
  transform: scale(1.02);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.message-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>
