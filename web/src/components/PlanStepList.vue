<script setup lang="ts">
import type { PlanDecisionOpenPayload, PlanStep } from '../plan/types'
import PlanDecisionPicker from './PlanDecisionPicker.vue'

defineProps<{ steps: PlanStep[] }>()
const emit = defineEmits<{ (e: 'open-decision', payload: PlanDecisionOpenPayload): void }>()
</script>

<template>
  <div class="plan-steps">
    <div v-for="(step, idx) in steps" :key="step.id" class="plan-step">
      <div class="step-index">{{ idx + 1 }}</div>
      <div class="step-body">
        <div class="step-title">{{ step.title }}</div>
        <div class="step-desc">{{ step.description }}</div>
        <div v-if="step.files?.length" class="step-files">
          <span v-for="f in step.files" :key="f">{{ f }}</span>
        </div>
        <ul v-if="step.validation?.length" class="step-validation">
          <li v-for="v in step.validation" :key="v">{{ v }}</li>
        </ul>
        <PlanDecisionPicker
          v-if="step.decisions?.length"
          :decisions="step.decisions"
          :step-id="step.id"
          @open-decision="emit('open-decision', { ...$event, stepTitle: step.title })"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.plan-steps { display: grid; gap: .7rem; }
.plan-step { display: grid; grid-template-columns: 1.65rem 1fr; gap: .7rem; }
.step-index { width: 1.65rem; height: 1.65rem; display: grid; place-items: center; border-radius: 999px; background: color-mix(in srgb, var(--accent) 16%, transparent); color: var(--accent); font-family: var(--font-mono); font-size: .72rem; font-weight: 900; }
.step-title { font-weight: 900; color: var(--fg); }
.step-desc { margin-top: .18rem; color: var(--fg-muted); line-height: 1.55; }
.step-files { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .45rem; }
.step-files span { padding: .12rem .42rem; border: 1px solid var(--surface-hairline); border-radius: 999px; color: var(--fg-dim); font-family: var(--font-mono); font-size: .68rem; }
.step-validation { margin: .45rem 0 0; padding-left: 1rem; color: var(--fg-muted); }
</style>
