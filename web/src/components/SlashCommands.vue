<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { getCommands, type CommandInfo } from '../api'

const props = defineProps<{
  input: string
  visible: boolean
}>()

const emit = defineEmits(['select', 'close'])
const commands = ref<CommandInfo[]>([])
const selectedIndex = ref(0)

const filtered = computed(() => {
  if (!props.input.startsWith('/')) return []
  const text = props.input.toLowerCase()
  return commands.value.filter(c => c.name.startsWith(text))
})

watch(filtered, () => {
  selectedIndex.value = 0
})

onMounted(async () => {
  try {
    const resp = await getCommands()
    commands.value = resp.commands
  } catch {}
})

function select(cmd: CommandInfo) {
  emit('select', cmd)
}

function moveUp() {
  if (selectedIndex.value > 0) selectedIndex.value--
}

function moveDown() {
  if (selectedIndex.value < filtered.value.length - 1) selectedIndex.value++
}

function confirm() {
  if (filtered.value[selectedIndex.value]) {
    select(filtered.value[selectedIndex.value])
  }
}

defineExpose({ moveUp, moveDown, confirm, filtered })
</script>

<template>
  <div v-if="visible && filtered.length > 0" class="slash-commands">
    <div
      v-for="(cmd, i) in filtered"
      :key="cmd.name"
      class="slash-item"
      :class="{ 'slash-item--selected': i === selectedIndex }"
      @click="select(cmd)"
      @mouseenter="selectedIndex = i"
    >
      <span class="slash-name">{{ cmd.name }}</span>
      <span class="slash-desc">{{ cmd.desc }}</span>
      <span v-if="cmd.has_arg" class="slash-arg">⟨{{ cmd.arg_name }}⟩</span>
    </div>
  </div>
</template>

<style scoped>
.slash-commands {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 -4px 16px var(--shadow);
  max-height: 240px;
  overflow-y: auto;
  z-index: 50;
  animation: fadeInUp 0.15s ease;
}

.slash-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.8rem;
  cursor: pointer;
  transition: background 0.15s ease;
}

.slash-item:hover,
.slash-item--selected {
  background: var(--bg-thinking);
}

.slash-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--accent);
  min-width: 5rem;
}

.slash-desc {
  font-size: 0.82rem;
  color: var(--fg-muted);
  flex: 1;
}

.slash-arg {
  font-size: 0.75rem;
  color: var(--fg-dim);
  font-style: italic;
}
</style>
