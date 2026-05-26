<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { initTheme, toggleTheme, type Theme } from './theme'
import { hasUsername, getUsername, setUsername } from './api'
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
  username: '',
})
const showSkills = ref(false)
const chatViewRef = ref<InstanceType<typeof ChatView>>()
const needUsername = ref(false)
const usernameInput = ref('')
const currentUsername = ref('')

onMounted(() => {
  theme.value = initTheme()
  if (hasUsername()) {
    currentUsername.value = getUsername()
    needUsername.value = false
  } else {
    needUsername.value = true
  }
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

function submitUsername() {
  const name = usernameInput.value.trim()
  if (!name) return
  setUsername(name)
  currentUsername.value = name
  needUsername.value = false
}
</script>

<template>
  <div v-if="needUsername" class="username-screen">
    <div class="username-card">
      <div class="username-icon">m</div>
      <h2 class="username-title">mini_ai</h2>
      <p class="username-sub">输入用户名开始对话</p>
      <div class="username-input-wrap">
        <input
          v-model="usernameInput"
          class="username-input"
          placeholder="用户名"
          autofocus
          @keydown.enter="submitUsername"
        />
        <button class="username-btn" :disabled="!usernameInput.trim()" @click="submitUsername">
          进入
        </button>
      </div>
    </div>
  </div>
  <template v-else>
    <header class="app-header">
      <div class="header-left">
        <h1 class="brand">mini_ai</h1>
        <span class="username-badge">{{ currentUsername }}</span>
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
</template>

<style scoped>
.username-screen {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
}

.username-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 3rem;
}

.username-icon {
  width: 72px;
  height: 72px;
  border-radius: 18px;
  background: var(--accent);
  color: var(--bg);
  font-family: 'Playfair Display', serif;
  font-weight: 700;
  font-size: 2.2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1.5rem;
}

.username-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--fg);
  margin-bottom: 0.5rem;
}

.username-title::after {
  content: '.';
  color: var(--accent);
}

.username-sub {
  color: var(--fg-muted);
  font-size: 0.95rem;
  margin-bottom: 2rem;
}

.username-input-wrap {
  display: flex;
  gap: 0.6rem;
  width: 320px;
}

.username-input {
  flex: 1;
  font-family: 'Source Sans 3', sans-serif;
  font-size: 1rem;
  padding: 0.6rem 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-input);
  color: var(--fg);
  outline: none;
  transition: border-color 0.2s ease;
}

.username-input:focus {
  border-color: var(--accent);
}

.username-btn {
  padding: 0.6rem 1.2rem;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font-size: 0.95rem;
  cursor: pointer;
  transition: background 0.2s ease;
}

.username-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}

.username-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

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

.username-badge {
  font-size: 0.78rem;
  color: var(--fg-dim);
  font-family: 'JetBrains Mono', monospace;
  padding: 0.15rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 4px;
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
