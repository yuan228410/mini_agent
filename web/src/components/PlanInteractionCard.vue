<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import type { PlanInteraction } from '../plan/types'

const props = defineProps<{ interaction: PlanInteraction }>()
const emit = defineEmits<{
  (e: 'submit', payload: { interaction: PlanInteraction; selectedIds: string[]; customValue: string }): void
}>()

const local = reactive({ selectedIds: [] as string[], customValue: '' })

watch(
  () => props.interaction,
  (next) => {
    local.selectedIds = [...(next.selectedIds || [])]
    local.customValue = next.customValue || ''
  },
  { immediate: true, deep: true }
)

const canSubmit = computed(() => props.interaction.completed || local.selectedIds.length > 0 || local.customValue.trim().length > 0)
const modeLabel = computed(() => {
  if (props.interaction.type === 'top_option') return '整体方案'
  if (props.interaction.allowMultiple) return '步骤多选'
  return '步骤决策'
})

function riskLevel(option: any): string {
  return option.risk_level || ''
}

function toggle(id: string) {
  if (props.interaction.completed) return
  if (props.interaction.allowMultiple) {
    local.selectedIds = local.selectedIds.includes(id)
      ? local.selectedIds.filter(v => v !== id)
      : [...local.selectedIds, id]
  } else {
    local.selectedIds = [id]
  }
}

function submit() {
  if (!canSubmit.value || props.interaction.completed) return
  emit('submit', {
    interaction: props.interaction,
    selectedIds: [...local.selectedIds],
    customValue: local.customValue.trim(),
  })
}
</script>

<template>
  <section class="plan-wizard-card" :class="{ completed: interaction.completed }">
    <div class="wizard-rail">
      <span class="rail-dot">{{ interaction.completed ? '✓' : '?' }}</span>
      <span class="rail-line"></span>
    </div>

    <div class="wizard-main">
      <header class="wizard-head">
        <div>
          <div class="eyebrow">PLAN WIZARD · {{ modeLabel }} · r{{ interaction.revision }}</div>
          <h3>{{ interaction.title }}</h3>
          <p v-if="interaction.description">{{ interaction.description }}</p>
          <p v-if="interaction.stepTitle" class="step-context">步骤：{{ interaction.stepTitle }}</p>
        </div>
        <span class="status-chip">{{ interaction.completed ? '已确认' : '等待选择' }}</span>
      </header>

      <div class="choice-grid">
        <button
          v-for="option in interaction.options"
          :key="option.id"
          class="choice-tile"
          :class="{
            active: local.selectedIds.includes(option.id),
            recommended: option.recommended,
            [`risk-${riskLevel(option)}`]: !!riskLevel(option),
          }"
          :disabled="interaction.completed"
          @click="toggle(option.id)"
        >
          <span class="select-mark">{{ local.selectedIds.includes(option.id) ? '✓' : '' }}</span>
          <span class="choice-main">
            <span class="choice-title">{{ option.title }}</span>
            <span v-if="option.recommended" class="badge">推荐</span>
            <span v-if="riskLevel(option)" class="risk">{{ riskLevel(option) }}</span>
            <span v-if="option.summary" class="choice-summary">{{ option.summary }}</span>
          </span>
        </button>
      </div>

      <label class="custom-box">
        <span>其他想法 / 都不满意</span>
        <textarea
          v-model="local.customValue"
          :disabled="interaction.completed"
          rows="3"
          placeholder="写下你的偏好、折中方案或额外约束…"
          @keydown.meta.enter.prevent="submit"
          @keydown.ctrl.enter.prevent="submit"
        ></textarea>
      </label>

      <footer class="wizard-actions">
        <span class="hint">{{ interaction.allowMultiple ? '可多选，也可以只填写其他想法' : '选择一个推荐/备选方案，或输入其他想法' }}</span>
        <button class="primary" :disabled="!canSubmit || interaction.completed" @click="submit">
          {{ interaction.completed ? '已提交' : (interaction.type === 'top_option' ? '确认方案，继续下一步' : '确认选择，更新计划') }}
        </button>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.plan-wizard-card {
  display: grid;
  grid-template-columns: 2rem 1fr;
  gap: .75rem;
  border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border));
  border-radius: 24px;
  padding: .9rem;
  background:
    radial-gradient(circle at 100% 0, color-mix(in srgb, var(--accent-glow) 22%, transparent), transparent 38%),
    linear-gradient(180deg, color-mix(in srgb, var(--bg-card) 88%, transparent), color-mix(in srgb, var(--bg-elevated) 62%, transparent));
  box-shadow: var(--shadow-card), inset 0 1px 0 rgba(255,255,255,.035);
}

.plan-wizard-card.completed {
  border-color: color-mix(in srgb, #3b9b62 42%, var(--border));
  opacity: .92;
}

.wizard-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: .15rem;
}

.rail-dot {
  width: 1.55rem;
  height: 1.55rem;
  border-radius: 999px;
  display: grid;
  place-items: center;
  border: 1px solid var(--accent);
  color: var(--accent);
  background: color-mix(in srgb, var(--accent-soft) 45%, var(--bg-card));
  font-weight: 900;
  box-shadow: 0 0 18px color-mix(in srgb, var(--accent-glow) 24%, transparent);
}

.completed .rail-dot {
  border-color: #3b9b62;
  color: #3b9b62;
  background: color-mix(in srgb, #3b9b62 12%, var(--bg-card));
}

.rail-line {
  width: 1px;
  flex: 1;
  min-height: 3.2rem;
  margin-top: .45rem;
  background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 45%, transparent), transparent);
}

.wizard-main { min-width: 0; }

.wizard-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  margin-bottom: .85rem;
}

.eyebrow {
  font-family: var(--font-mono);
  color: var(--accent);
  font-size: .68rem;
  font-weight: 900;
  letter-spacing: .12em;
}

h3 {
  margin: .18rem 0 0;
  color: var(--fg);
  font-size: 1.06rem;
}

p {
  margin: .32rem 0 0;
  color: var(--fg-muted);
  line-height: 1.55;
}

.step-context {
  font-family: var(--font-mono);
  font-size: .72rem;
  color: var(--fg-dim);
}

.status-chip {
  flex-shrink: 0;
  padding: .18rem .52rem;
  border: 1px solid var(--surface-hairline);
  border-radius: 999px;
  color: var(--fg-muted);
  font-family: var(--font-mono);
  font-size: .66rem;
  font-weight: 900;
}

.choice-grid {
  display: grid;
  gap: .58rem;
}

.choice-tile {
  display: grid;
  grid-template-columns: 1.5rem 1fr;
  gap: .62rem;
  width: 100%;
  text-align: left;
  padding: .76rem;
  border: 1px solid var(--surface-hairline);
  border-radius: 18px;
  background: color-mix(in srgb, var(--bg-card) 72%, transparent);
  color: var(--fg);
  cursor: pointer;
  transition: transform .16s var(--ease-out), border-color .16s var(--ease-out), background .16s var(--ease-out), box-shadow .16s var(--ease-out);
}

.choice-tile:not(:disabled):hover {
  transform: translateY(-1px);
  border-color: color-mix(in srgb, var(--accent) 46%, var(--border));
  box-shadow: 0 10px 24px color-mix(in srgb, var(--shadow) 18%, transparent);
}

.choice-tile.active {
  border-color: var(--accent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 24%, transparent);
}

.choice-tile.recommended {
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent-soft) 42%, transparent), color-mix(in srgb, var(--bg-card) 78%, transparent));
}

.choice-tile:disabled {
  cursor: default;
}

.select-mark {
  width: 1.26rem;
  height: 1.26rem;
  display: grid;
  place-items: center;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--accent) 38%, var(--border));
  color: var(--accent);
  font-weight: 900;
}

.choice-main { min-width: 0; }
.choice-title { font-weight: 900; color: var(--fg); }
.badge,
.risk {
  margin-left: .42rem;
  padding: .08rem .36rem;
  border-radius: 999px;
  font-family: var(--font-mono);
  font-size: .62rem;
  font-weight: 900;
}
.badge { background: var(--accent); color: var(--accent-ink); }
.risk { color: var(--fg-dim); border: 1px solid var(--surface-hairline); text-transform: uppercase; }
.risk-low .risk { color: #3b9b62; }
.risk-medium .risk { color: var(--accent); }
.risk-high .risk { color: #d14b45; }
.choice-summary { display: block; margin-top: .22rem; color: var(--fg-muted); line-height: 1.48; }

.custom-box {
  display: grid;
  gap: .42rem;
  margin-top: .78rem;
  color: var(--fg-muted);
  font-size: .82rem;
  font-weight: 800;
}

.custom-box textarea {
  resize: vertical;
  min-height: 76px;
  border: 1px solid var(--surface-hairline);
  border-radius: 18px;
  background: var(--bg-input);
  color: var(--fg);
  padding: .72rem .82rem;
  outline: none;
  line-height: 1.55;
}

.custom-box textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-soft) 42%, transparent);
}

.wizard-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .75rem;
  margin-top: .85rem;
}

.hint {
  color: var(--fg-dim);
  font-size: .76rem;
  line-height: 1.4;
}

.primary {
  flex-shrink: 0;
  border: 1px solid var(--accent);
  border-radius: 999px;
  padding: .55rem .85rem;
  background: var(--accent);
  color: var(--accent-ink);
  font-weight: 900;
  cursor: pointer;
  transition: transform .16s var(--ease-out), box-shadow .16s var(--ease-out), opacity .16s;
}

.primary:not(:disabled):hover {
  transform: translateY(-1px);
  box-shadow: var(--glow-accent);
}

.primary:disabled {
  opacity: .48;
  cursor: not-allowed;
}

@media (max-width: 720px) {
  .plan-wizard-card { grid-template-columns: 1fr; }
  .wizard-rail { display: none; }
  .wizard-head,
  .wizard-actions { flex-direction: column; align-items: stretch; }
}
</style>
