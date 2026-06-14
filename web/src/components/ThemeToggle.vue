<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { THEMES, getThemeMeta, type Theme } from '../theme'

const props = defineProps<{ theme: Theme }>()
const emit = defineEmits<{ select: [theme: Theme] }>()

const open = ref(false)
const currentTheme = computed(() => getThemeMeta(props.theme))
const lightThemes = computed(() => THEMES.filter(theme => theme.mode === 'light'))
const darkThemes = computed(() => THEMES.filter(theme => theme.mode === 'dark'))

function selectTheme(theme: Theme) {
  emit('select', theme)
  open.value = false
}

function close() {
  open.value = false
  document.removeEventListener('click', onDocumentClick)
}

function onDocumentClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (!target?.closest('.theme-picker')) close()
}

function toggle() {
  if (open.value) {
    close()
    return
  }
  open.value = true
  setTimeout(() => document.addEventListener('click', onDocumentClick), 0)
}

onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div class="theme-picker">
    <button class="theme-trigger" @click="toggle" :title="`当前主题：${currentTheme.name}`" :aria-expanded="open">
      <span class="theme-swatch" :style="{ background: currentTheme.accent }"></span>
      <span class="theme-icon">{{ currentTheme.icon }}</span>
      <span class="theme-name">{{ currentTheme.name }}</span>
      <span class="theme-caret" :class="{ open }">⌄</span>
    </button>

    <div v-if="open" class="theme-menu">
      <div class="theme-group">
        <div class="theme-group-title">亮色</div>
        <button
          v-for="item in lightThemes"
          :key="item.id"
          class="theme-option"
          :class="{ active: item.id === theme }"
          @click="selectTheme(item.id)"
        >
          <span class="theme-swatch" :style="{ background: item.accent }"></span>
          <span class="theme-option-main">
            <span class="theme-option-name">{{ item.icon }} {{ item.name }}</span>
            <span class="theme-option-desc">{{ item.description }}</span>
          </span>
          <span class="theme-check">{{ item.id === theme ? '✓' : '' }}</span>
        </button>
      </div>

      <div class="theme-group">
        <div class="theme-group-title">暗色</div>
        <button
          v-for="item in darkThemes"
          :key="item.id"
          class="theme-option"
          :class="{ active: item.id === theme }"
          @click="selectTheme(item.id)"
        >
          <span class="theme-swatch" :style="{ background: item.accent }"></span>
          <span class="theme-option-main">
            <span class="theme-option-name">{{ item.icon }} {{ item.name }}</span>
            <span class="theme-option-desc">{{ item.description }}</span>
          </span>
          <span class="theme-check">{{ item.id === theme ? '✓' : '' }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.theme-picker {
  position: relative;
}

.theme-trigger {
  height: 36px;
  min-width: 92px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-card);
  color: var(--fg);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 7px;
  transition: all 0.2s ease;
}

.theme-trigger:hover,
.theme-trigger[aria-expanded="true"] {
  border-color: var(--accent);
  background: var(--bg-hover);
}

.theme-swatch {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  box-shadow: 0 0 0 2px var(--accent-soft);
  flex-shrink: 0;
}

.theme-icon {
  font-size: 0.95rem;
  line-height: 1;
}

.theme-name {
  font-size: 0.78rem;
  font-weight: 650;
  white-space: nowrap;
}

.theme-caret {
  margin-left: auto;
  color: var(--fg-muted);
  font-size: 0.75rem;
  transition: transform 0.2s ease;
}

.theme-caret.open {
  transform: rotate(180deg);
}

.theme-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 286px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--bg-input);
  box-shadow: 0 16px 42px var(--shadow);
  z-index: 120;
  animation: themeMenuIn 0.16s ease;
}

.theme-group + .theme-group {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border-light);
}

.theme-group-title {
  margin: 0 0 6px 4px;
  color: var(--fg-dim);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.theme-option {
  width: 100%;
  padding: 8px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--fg);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 9px;
  text-align: left;
  transition: all 0.16s ease;
}

.theme-option:hover,
.theme-option.active {
  border-color: var(--border);
  background: var(--bg-hover);
}

.theme-option.active {
  border-color: var(--accent);
}

.theme-option-main {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.theme-option-name {
  font-size: 0.78rem;
  font-weight: 700;
}

.theme-option-desc {
  overflow: hidden;
  color: var(--fg-muted);
  font-size: 0.68rem;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.theme-check {
  width: 14px;
  color: var(--accent);
  font-size: 0.78rem;
  font-weight: 800;
  text-align: center;
}

@keyframes themeMenuIn {
  from { opacity: 0; transform: translateY(-4px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
</style>
