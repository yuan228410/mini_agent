<script setup lang="ts">
import type { PlanOption } from '../plan/types'

defineProps<{ options: PlanOption[]; selectedId?: string | null }>()
const emit = defineEmits<{ (e: 'open-choice'): void }>()
</script>

<template>
  <div class="plan-options">
    <div v-for="opt in options" :key="opt.id" class="plan-option" :class="{ selected: selectedId === opt.id, recommended: opt.recommended }">
      <div class="option-head">
        <div>
          <span class="option-title">{{ opt.title }}</span>
          <span v-if="opt.recommended" class="option-badge">推荐</span>
        </div>
        <span class="risk" :class="`risk-${opt.risk_level}`">{{ opt.risk_level }}</span>
      </div>
      <p>{{ opt.summary }}</p>
      <div class="option-grid">
        <div v-if="opt.pros?.length"><b>优点</b><ul><li v-for="p in opt.pros" :key="p">{{ p }}</li></ul></div>
        <div v-if="opt.cons?.length"><b>注意</b><ul><li v-for="c in opt.cons" :key="c">{{ c }}</li></ul></div>
      </div>
      <button class="option-select" @click="emit('open-choice')">{{ selectedId === opt.id ? '已选择 · 重新确认' : '打开弹框选择' }}</button>
    </div>
  </div>
</template>

<style scoped>
.plan-options { display: grid; gap: .7rem; }
.plan-option { padding: .8rem; border: 1px solid var(--surface-hairline); border-radius: 18px; background: color-mix(in srgb, var(--bg-card) 72%, transparent); }
.plan-option.selected { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 18%, transparent); }
.plan-option.recommended { background: linear-gradient(135deg, color-mix(in srgb, var(--accent-soft) 46%, transparent), color-mix(in srgb, var(--bg-card) 76%, transparent)); }
.option-head { display: flex; justify-content: space-between; gap: .75rem; align-items: center; }
.option-title { font-weight: 900; color: var(--fg); }
.option-badge { margin-left: .45rem; padding: .08rem .38rem; border-radius: 999px; background: var(--accent); color: var(--bg); font-size: .65rem; font-weight: 900; }
p { margin: .45rem 0; color: var(--fg-muted); line-height: 1.55; }
.risk { font-family: var(--font-mono); font-size: .65rem; text-transform: uppercase; }
.risk-low { color: #3b9b62; } .risk-medium { color: var(--accent); } .risk-high { color: #d14b45; }
.option-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: .55rem; color: var(--fg-muted); font-size: .85rem; }
ul { margin: .25rem 0 0; padding-left: 1rem; }
.option-select { margin-top: .55rem; padding: .35rem .62rem; border: 1px solid var(--surface-hairline); border-radius: 999px; background: var(--surface-control); color: var(--fg); font-weight: 800; cursor: pointer; }
.option-select:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
</style>
