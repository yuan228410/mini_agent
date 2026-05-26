<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getSkills } from '../api'

interface Skill {
  name: string
  description: string
}

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits(['close', 'use'])
const skills = ref<Skill[]>([])

onMounted(async () => {
  await refresh()
})

async function refresh() {
  try {
    const resp = await getSkills()
    if (resp.skills && typeof resp.skills === 'string') {
      skills.value = resp.skills
        .split('\n')
        .filter((s: string) => s.trim() && !s.trim().startsWith('('))
        .map((s: string) => ({ name: s.trim(), description: '' }))
    } else if (Array.isArray(resp.skills)) {
      skills.value = resp.skills
    }
  } catch {}
}

function useSkill(name: string) {
  emit('use', name)
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="skill-overlay" @click="emit('close')">
      <div class="skill-panel" @click.stop>
        <div class="skill-header">
          <h3 class="skill-title">技能</h3>
          <button class="skill-close" @click="emit('close')">✕</button>
        </div>
        <div class="skill-list">
          <div v-if="skills.length === 0" class="skill-empty">暂无技能</div>
          <div
            v-for="skill in skills"
            :key="skill.name"
            class="skill-item"
            @click="useSkill(skill.name)"
          >
            <span class="skill-item-name">{{ skill.name }}</span>
            <span v-if="skill.description" class="skill-item-desc">{{ skill.description }}</span>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.skill-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  z-index: 200;
  animation: fadeIn 0.2s ease;
}

.skill-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 320px;
  background: var(--bg);
  border-left: 0.5px solid var(--border);
  box-shadow: -4px 0 20px var(--shadow);
  display: flex;
  flex-direction: column;
  animation: slideIn 0.25s ease;
}

@keyframes slideIn {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.skill-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.2rem;
  border-bottom: 0.5px solid var(--border);
}

.skill-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--fg);
}

.skill-close {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  color: var(--fg-dim);
  font-size: 1rem;
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.skill-close:hover {
  background: var(--bg-card);
  color: var(--fg);
}

.skill-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0;
}

.skill-empty {
  padding: 2rem;
  text-align: center;
  color: var(--fg-dim);
  font-size: 0.9rem;
}

.skill-item {
  padding: 0.7rem 1.2rem;
  cursor: pointer;
  transition: background 0.15s ease;
  border-bottom: 0.5px solid var(--border-light);
}

.skill-item:hover {
  background: var(--bg-thinking);
}

.skill-item-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--fg);
  display: block;
}

.skill-item-desc {
  font-size: 0.8rem;
  color: var(--fg-dim);
  display: block;
  margin-top: 0.2rem;
}
</style>
