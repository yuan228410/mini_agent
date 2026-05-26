<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getModels, switchModel } from '../api'

interface ModelItem {
  name: string
  model: string
}

const models = ref<ModelItem[]>([])
const activeName = ref('')
const activeModel = ref('')
const open = ref(false)
const props = defineProps<{ sessionId?: string }>()
const emit = defineEmits(['switched'])

onMounted(async () => {
  await refresh()
})

async function refresh() {
  try {
    const resp = await getModels()
    models.value = resp.models
    activeName.value = resp.active_name
    activeModel.value = resp.active
  } catch {}
}

async function select(name: string) {
  open.value = false
  if (name === activeName.value) return
  try {
    const resp = await switchModel(name, props.sessionId)
    if (resp.error) return
    activeName.value = name
    activeModel.value = resp.model || name
    emit('switched')
  } catch {}
}

function toggle() {
  open.value = !open.value
}

function onClickOutside(e: MouseEvent) {
  open.value = false
}
</script>

<template>
  <div class="model-selector" v-click-outside="onClickOutside">
    <button class="model-btn" @click="toggle" :title="`当前模型: ${activeModel}`">
      <span class="model-label">⚙</span>
      <span class="model-name">{{ activeName }}</span>
      <span class="model-arrow" :class="{ 'model-arrow--open': open }">▾</span>
    </button>
    <div v-if="open" class="model-dropdown">
      <div
        v-for="m in models"
        :key="m.name"
        class="model-option"
        :class="{ 'model-option--active': m.name === activeName }"
        @click="select(m.name)"
      >
        <span class="model-option-name">{{ m.name }}</span>
        <span class="model-option-id">{{ m.model }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.model-selector {
  position: relative;
}

.model-btn {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
  cursor: pointer;
  transition: all 0.2s ease;
  font-family: 'Source Sans 3', sans-serif;
}

.model-btn:hover {
  border-color: var(--accent);
}

.model-label {
  font-size: 0.85rem;
}

.model-name {
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--fg);
}

.model-arrow {
  font-size: 0.65rem;
  color: var(--fg-dim);
  transition: transform 0.2s ease;
}

.model-arrow--open {
  transform: rotate(180deg);
}

.model-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  min-width: 200px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 4px 16px var(--shadow);
  z-index: 100;
  overflow: hidden;
  animation: fadeInUp 0.15s ease;
}

.model-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 0.8rem;
  cursor: pointer;
  transition: background 0.15s ease;
}

.model-option:hover {
  background: var(--bg-thinking);
}

.model-option--active {
  background: var(--bg-thinking);
}

.model-option--active .model-option-name {
  color: var(--accent);
  font-weight: 600;
}

.model-option-name {
  font-size: 0.85rem;
  color: var(--fg);
}

.model-option-id {
  font-size: 0.75rem;
  color: var(--fg-dim);
  font-family: 'JetBrains Mono', monospace;
}
</style>
