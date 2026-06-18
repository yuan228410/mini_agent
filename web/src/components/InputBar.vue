<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import SlashCommands from './SlashCommands.vue'
import type { CommandInfo } from '../api'

export interface ImageFile {
  dataUrl: string
  name: string
  size: number
}

const props = defineProps<{ disabled?: boolean; isStreaming?: boolean; modelValue?: string; mode?: 'chat' | 'planning' | 'awaiting_approval' | 'executing' }>()
const emit = defineEmits<{
  (e: 'send', text: string, images?: ImageFile[]): void
  (e: 'stop'): void
  (e: 'update:modelValue', value: string): void
}>()
const textareaEl = ref<HTMLTextAreaElement>()
const text = ref(props.modelValue || '')
const slashRef = ref<InstanceType<typeof SlashCommands>>()
const fileInputEl = ref<HTMLInputElement>()
const images = ref<ImageFile[]>([])

watch(text, (val) => {
  emit('update:modelValue', val)
})

watch(() => props.modelValue, (val) => {
  if (val !== text.value) {
    text.value = val || ''
    nextTick(() => autoResize())
  }
})

const showSlash = computed(() => text.value.startsWith('/') && text.value.length < 30)
const placeholder = computed(() => {
  if (props.mode === 'planning') return '按消息区向导选择，或继续补充约束/修改想法…'
  if (props.mode === 'awaiting_approval') return '继续提出修改，或点击“批准并执行”…'
  if (props.mode === 'executing') return '正在执行已批准计划…'
  return '输入消息… / 命令…'
})

function submit() {
  const val = text.value.trim()
  if (props.disabled || props.mode === 'executing') return
  if (!val && images.value.length === 0) return
  emit('send', val, images.value.length > 0 ? [...images.value] : undefined)
  text.value = ''
  images.value = []
}

function triggerFileInput() {
  fileInputEl.value?.click()
}

function onFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  const files = input.files
  if (!files) return
  
  for (const file of files) {
    if (!file.type.startsWith('image/')) continue
    if (file.size > 10 * 1024 * 1024) {
      alert(`图片 ${file.name} 超过 10MB，请压缩后上传`)
      continue
    }
    
    const reader = new FileReader()
    reader.onload = () => {
      images.value.push({
        dataUrl: reader.result as string,
        name: file.name,
        size: file.size
      })
    }
    reader.readAsDataURL(file)
  }
  
  // 重置 input，允许重复选择同一文件
  input.value = ''
}

function removeImage(index: number) {
  images.value.splice(index, 1)
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
    <!-- 图片预览 -->
    <div v-if="images.length > 0" class="image-preview">
      <div v-for="(img, i) in images" :key="i" class="image-thumb">
        <img :src="img.dataUrl" :alt="img.name" />
        <button class="remove-btn" @click="removeImage(i)" title="移除">×</button>
      </div>
    </div>
    
    <div class="input-wrap">
      <!-- 图片上传按钮 -->
      <button class="attach-btn" @click="triggerFileInput" :disabled="disabled" title="添加图片">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
          <circle cx="8.5" cy="8.5" r="1.5"></circle>
          <polyline points="21 15 16 10 5 21"></polyline>
        </svg>
      </button>
      <input ref="fileInputEl" type="file" accept="image/*" multiple hidden @change="onFileSelect" />
      
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
          :placeholder="placeholder"
          rows="1"
          @keydown="onKeydown"
          @input="autoResize"
          ref="textareaEl"
        ></textarea>
      </div>
      <button v-if="isStreaming" class="stop-btn" @click="$emit('stop')" title="停止生成">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
      </button>
      <button v-else class="send-btn" :disabled="disabled || mode === 'executing' || (!text.trim() && images.length === 0)" @click="submit">
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
  margin: 0 .9rem .85rem;
  padding: .65rem;
  border: 1px solid var(--surface-hairline);
  border-radius: 24px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--bg-card) 76%, transparent), color-mix(in srgb, var(--bg) 88%, transparent));
  box-shadow: var(--shadow-soft), inset 0 1px 0 rgba(255,255,255,.04);
  backdrop-filter: blur(18px) saturate(1.08);
  position: relative;
}

.input-bar::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: radial-gradient(circle at 82% 0, color-mix(in srgb, var(--accent-glow) 24%, transparent), transparent 34%);
  pointer-events: none;
}

.input-bar > * {
  position: relative;
}

.image-preview {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem 0;
  overflow-x: auto;
  flex-wrap: wrap;
}

.image-thumb {
  position: relative;
  width: 60px;
  height: 60px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--border);
  flex-shrink: 0;
}

.image-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.remove-btn {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: white;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.2s;
}

.image-thumb:hover .remove-btn {
  opacity: 1;
}

.remove-btn:hover {
  background: rgba(220, 53, 69, 0.9);
}

.input-wrap {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0;
}

.attach-btn {
  flex-shrink: 0;
  width: 42px;
  height: 42px;
  border: 1px solid var(--surface-hairline);
  border-radius: 16px;
  background: var(--surface-control);
  color: var(--fg-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all .16s var(--ease-out);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.035);
}

.attach-btn:hover:not(:disabled) {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 42%, var(--border));
  transform: translateY(-1px);
}

.attach-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.input-field {
  flex: 1;
  position: relative;
}

textarea {
  width: 100%;
  font-family: var(--font-ui);
  font-size: 1rem;
  line-height: 1.5;
  color: var(--fg);
  background: color-mix(in srgb, var(--bg-input) 90%, transparent);
  border: 1px solid color-mix(in srgb, var(--border) 58%, transparent);
  border-radius: 18px;
  padding: 0.58rem 0.9rem;
  resize: none;
  outline: none;
  transition: all .16s var(--ease-out);
  min-height: 44px;
  max-height: 160px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.03);
}

textarea:focus {
  border-color: color-mix(in srgb, var(--accent) 54%, var(--border));
  background: color-mix(in srgb, var(--bg-input) 98%, transparent);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent-soft) 52%, transparent), inset 0 1px 0 rgba(255,255,255,.04);
}

textarea::placeholder {
  color: var(--fg-dim);
}

.send-btn {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  margin-bottom: 1px;
  border: 1px solid transparent;
  border-radius: 16px;
  background: linear-gradient(135deg, var(--accent), var(--accent-hover));
  color: var(--accent-ink);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all .14s var(--ease-out);
  box-shadow: 0 14px 32px color-mix(in srgb, var(--shadow) 26%, transparent);
}

.send-btn:hover:not(:disabled) {
  filter: saturate(1.08);
  transform: translateY(-1px);
  box-shadow: var(--glow-accent);
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
  width: 44px;
  height: 44px;
  margin-bottom: 1px;
  border: none;
  border-radius: 16px;
  background: linear-gradient(135deg, #e55, color-mix(in srgb, #e55 76%, #fff));
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
