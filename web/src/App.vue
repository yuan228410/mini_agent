<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { initTheme, toggleTheme, type Theme } from './theme'
import ChatView from './components/ChatView.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import StatusBar from './components/StatusBar.vue'
import ModelSelector from './components/ModelSelector.vue'
import SkillPanel from './components/SkillPanel.vue'

const theme = ref<Theme>('light')
const config = ref({
  model: '?',
  context_length: 128000,
  prompt_tokens: 0,
  completion_tokens: 0,
  system_prompt_chars: 0,
  history_count: 0,
  session_id: '',
})
const showSkills = ref(false)
const chatViewRef = ref<InstanceType<typeof ChatView>>()

onMounted(() => {
  theme.value = initTheme()
})

function onToggleTheme() {
  theme.value = toggleTheme(theme.value)
}

function onConfigUpdate(c: any) {
  config.value = c
}

function onModelSwitched() {
  config.value.model = '(switched)'
}

function onUseSkill(name: string) {
  chatViewRef.value?.useSkill(name)
}
</script>

<template>
  <header class="app-header">
    <div class="header-left">
      <h1 class="brand">mini_ai</h1>
    </div>
    <div class="header-right">
      <ModelSelector @switched="onModelSwitched" />
      <button class="skill-btn" @click="showSkills = true" title="技能面板">
        <span>🔧</span>
      </button>
      <ThemeToggle :theme="theme" @toggle="onToggleTheme" />
    </div>
  </header>
  <ChatView ref="chatViewRef" @config-update="onConfigUpdate" />
  <StatusBar v-bind="config" />
  <SkillPanel :visible="showSkills" @close="showSkills = false" @use="onUseSkill" />
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.8rem 1.5rem;
  border-bottom: 0.5px solid var(--border);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.brand {
  font-family: 'Playfair Display', serif;
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--fg);
  letter-spacing: -0.02em;
}

.brand::after {
  content: '.';
  color: var(--accent);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

.skill-btn {
  width: 36px;
  height: 36px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-card);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  font-size: 0.95rem;
}

.skill-btn:hover {
  border-color: var(--accent);
  background: var(--bg-thinking);
}
</style>
