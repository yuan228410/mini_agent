<script setup lang="ts">
import type { Theme } from '../theme'

defineProps<{ theme: Theme }>()
const emit = defineEmits(['toggle'])
</script>

<template>
  <button class="theme-toggle" @click="emit('toggle')" :title="theme === 'light' ? '切换暗色主题' : '切换亮色主题'">
    <span class="theme-icon" :class="{ 'rotate': theme === 'dark' }">
      {{ theme === 'light' ? '☀' : '🌙' }}
    </span>
  </button>
</template>

<style scoped>
.theme-toggle {
  width: 36px;
  height: 36px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-card);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
}

.theme-toggle:hover {
  border-color: var(--accent);
  background: var(--bg-thinking);
}

.theme-icon {
  font-size: 1rem;
  transition: transform 0.4s ease;
}

.theme-icon.rotate {
  transform: rotate(360deg);
}
</style>
