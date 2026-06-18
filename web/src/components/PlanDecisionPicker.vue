<script setup lang="ts">
import type { PlanDecision } from '../plan/types'

const props = defineProps<{ decisions: PlanDecision[]; stepId?: string }>()
const emit = defineEmits<{ (e: 'open-decision', payload: { decision: PlanDecision; stepId?: string }): void }>()
</script>

<template>
  <div class="decision-list">
    <div v-for="decision in decisions" :key="decision.id" class="decision-card">
      <div class="decision-head">
        <div>
          <div class="decision-title">{{ decision.title }}</div>
          <p v-if="decision.description">{{ decision.description }}</p>
        </div>
        <span class="mode-pill">{{ decision.allow_multiple ? '多选' : '单选' }}</span>
      </div>

      <div class="choice-grid">
        <div
          v-for="choice in decision.options"
          :key="choice.id"
          class="choice"
          :class="{ active: (decision.selected_option_ids || []).includes(choice.id), recommended: choice.recommended }"
        >
          <span class="choice-title">{{ choice.title }}</span>
          <span v-if="choice.recommended" class="choice-badge">推荐</span>
          <span v-if="choice.summary" class="choice-summary">{{ choice.summary }}</span>
        </div>
      </div>

      <button class="open-dialog" @click="emit('open-decision', { decision, stepId: props.stepId })">
        弹框选择 / 输入其他想法
      </button>
    </div>
  </div>
</template>

<style scoped>
.decision-list { display: grid; gap: .55rem; margin-top: .55rem; }
.decision-card { padding: .65rem; border: 1px dashed color-mix(in srgb, var(--accent) 34%, var(--border)); border-radius: 16px; background: color-mix(in srgb, var(--accent-soft) 22%, transparent); }
.decision-head { display: flex; justify-content: space-between; gap: .75rem; align-items: flex-start; }
.decision-title { font-weight: 900; color: var(--fg); }
p { margin: .18rem 0 0; color: var(--fg-muted); line-height: 1.5; }
.mode-pill { flex-shrink: 0; padding: .12rem .42rem; border-radius: 999px; border: 1px solid var(--surface-hairline); color: var(--accent); font-family: var(--font-mono); font-size: .65rem; font-weight: 900; }
.choice-grid { display: grid; gap: .42rem; margin-top: .55rem; }
.choice { text-align: left; padding: .55rem .62rem; border: 1px solid var(--surface-hairline); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 72%, transparent); color: var(--fg); }
.choice.active { border-color: var(--accent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 22%, transparent); }
.choice.recommended { background: linear-gradient(135deg, color-mix(in srgb, var(--accent-soft) 42%, transparent), color-mix(in srgb, var(--bg-card) 72%, transparent)); }
.choice-title { font-weight: 900; }
.choice-badge { margin-left: .4rem; padding: .06rem .32rem; border-radius: 999px; background: var(--accent); color: var(--bg); font-size: .62rem; font-weight: 900; }
.choice-summary { display: block; margin-top: .18rem; color: var(--fg-muted); font-size: .82rem; line-height: 1.45; }
.open-dialog { margin-top: .55rem; border: 1px solid var(--accent); border-radius: 999px; background: var(--accent); color: var(--accent-ink); font-weight: 900; padding: .42rem .65rem; cursor: pointer; }
.open-dialog:hover { filter: brightness(1.05); }
</style>
