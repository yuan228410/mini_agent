<script setup lang="ts">
import { ref, computed } from 'vue'
import SlashCommands from './SlashCommands.vue'
import type { CommandInfo } from '../api'

const props = defineProps<{ disabled?: boolean; isStreaming?: boolean }>()
const emit = defineEmits(['send', 'stop'])
const textareaEl = ref<HTMLTextAreaElement>()
const text = ref('')
const slashRef = ref<InstanceType<typeof SlashCommands>>()

const showSlash = computed(() => text.value.startsWith('/') && text.value.length < 30)

function submit() {
  const val = text.value.trim()
  if (!val || props.disabled) return
  emit('send', val)
  text.value = ''
}

function autoResize(e?: Event) {
  const el = e?.target as HTMLTextAreaElement || textareaEl.value
  if (!el) return
  el.style.height = "auto"
  el.style.height = Math.min(el.scrollHeight, 160) + "px"
}

function onKeydown(e: KeyboardEvent) {
  if (showSlash.value) {
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      slashRef.value?.moveUp()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      slashRef.value?.moveDown()
      return
    }
    if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && !e.isComposing)) {
      e.preventDefault()
      slashRef.value?.confirm()
      return
    }
    if (e.key === 'Escape') {
      text.value = ''
      return
    }
  }

  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}

function onSlashSelect(cmd: CommandInfo) {
  if (cmd.has_arg) {
    text.value = cmd.name + ' '
  } else {
    emit('send', cmd.name)
    text.value = ''
  }
}
</script>

<template>
  <div class="input-bar">
    <div class="input-wrap">
      <div class="input-field">
        <SlashCommands
          ref="slashRef"
          :input="text"
          :visible="showSlash"
          @select="onSlashSelect"
        />
        <textarea
          v-model="text"
          :disabled="disabled"
          placeholder="输入消息… / 命令…"
          rows="1"
          @keydown="onKeydown"
          @input="autoResize"
          ref="textareaEl"
        ></textarea>
      </div>
      <button v-if="isStreaming" class="stop-btn" @click="$emit('stop')" title="停止生成">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
      </button>
      <button v-else class="send-btn" :disabled="disabled || !text.trim()" @click="submit">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="22" y1="2" x2="11" y2="13"></line>
          <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.input-bar {
  flex-shrink: 0;
  padding: 0.3rem 0.5rem 0.1rem;
  border-top: 0.5px solid var(--border);
  background: var(--bg);
}

.input-wrap {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0;
}

.input-field {
  flex: 1;
  position: relative;
}

textarea {
  width: 100%;
  font-family: 'Source Sans 3', sans-serif;
  font-size: 1rem;
  line-height: 1.5;
  color: var(--fg);
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.45rem 0.8rem;
  resize: none;
  outline: none;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  min-height: 42px;
  max-height: 160px;
}

textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(232, 145, 45, 0.15);
}

textarea::placeholder {
  color: var(--fg-dim);
}

.send-btn {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  margin-bottom: 1px;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease, transform 0.15s ease, box-shadow 0.2s ease;
}

.send-btn:hover:not(:disabled) {
  background: var(--accent-hover);
  transform: scale(1.05);
  box-shadow: 0 2px 12px rgba(232, 145, 45, 0.3);
}

.send-btn:active:not(:disabled) {
  transform: scale(0.97);
}

.send-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.stop-btn {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  margin-bottom: 1px;
  border: none;
  border-radius: 8px;
  background: #e55;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease, transform 0.1s ease;
}

.stop-btn:hover {
  background: #c44;
  transform: scale(1.03);
}
</style>
