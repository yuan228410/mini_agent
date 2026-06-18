<script setup lang="ts">
import type { PlanArtifact, PlanDecisionOpenPayload } from '../plan/types'
import PlanOptionPicker from './PlanOptionPicker.vue'
import PlanStepList from './PlanStepList.vue'

defineProps<{ plan: PlanArtifact }>()
const emit = defineEmits<{
  (e: 'open-option'): void
  (e: 'open-decision', payload: PlanDecisionOpenPayload): void
}>()
</script>

<template>
  <section class="plan-card">
    <div class="plan-card-head">
      <div>
        <div class="eyebrow">PLAN WORKFLOW · r{{ plan.revision }}</div>
        <h3>{{ plan.goal }}</h3>
      </div>
      <span class="state-pill">{{ plan.status }}</span>
    </div>
    <p class="summary">{{ plan.summary }}</p>

    <div v-if="plan.open_questions?.length" class="block questions">
      <h4>需要确认</h4>
      <ul><li v-for="q in plan.open_questions" :key="q">{{ q }}</li></ul>
    </div>

    <div v-if="plan.options?.length" class="block">
      <h4>方案选项</h4>
      <PlanOptionPicker :options="plan.options" :selected-id="plan.selected_option_id" @open-choice="emit('open-option')" />
    </div>

    <div v-if="plan.steps?.length" class="block">
      <h4>执行步骤</h4>
      <PlanStepList :steps="plan.steps" @open-decision="emit('open-decision', $event)" />
    </div>

    <div class="plan-grid">
      <div v-if="plan.risks?.length" class="block compact"><h4>风险</h4><ul><li v-for="r in plan.risks" :key="r">{{ r }}</li></ul></div>
      <div v-if="plan.validation_strategy?.length" class="block compact"><h4>验证</h4><ul><li v-for="v in plan.validation_strategy" :key="v">{{ v }}</li></ul></div>
    </div>
  </section>
</template>

<style scoped>
.plan-card { border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border)); border-radius: 24px; padding: 1rem; background: radial-gradient(circle at 90% 0, color-mix(in srgb, var(--accent-glow) 24%, transparent), transparent 38%), linear-gradient(180deg, color-mix(in srgb, var(--bg-card) 82%, transparent), color-mix(in srgb, var(--bg-elevated) 68%, transparent)); box-shadow: var(--shadow-card); }
.plan-card-head { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; }
.eyebrow { font-family: var(--font-mono); color: var(--accent); font-size: .68rem; font-weight: 900; letter-spacing: .08em; }
h3 { margin: .2rem 0 0; color: var(--fg); font-size: 1.05rem; }
.state-pill { padding: .18rem .5rem; border: 1px solid var(--surface-hairline); border-radius: 999px; color: var(--fg-muted); font-family: var(--font-mono); font-size: .68rem; }
.summary { color: var(--fg-muted); line-height: 1.6; }
.block { margin-top: .9rem; }
h4 { margin: 0 0 .45rem; font-size: .78rem; color: var(--fg); letter-spacing: .05em; text-transform: uppercase; }
ul { margin: 0; padding-left: 1.1rem; color: var(--fg-muted); line-height: 1.55; }
.questions { padding: .75rem; border: 1px dashed color-mix(in srgb, var(--accent) 40%, var(--border)); border-radius: 16px; background: color-mix(in srgb, var(--accent-soft) 35%, transparent); }
.plan-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .75rem; }
.compact { padding: .75rem; border: 1px solid var(--surface-hairline); border-radius: 16px; background: color-mix(in srgb, var(--bg-card) 50%, transparent); }
</style>
