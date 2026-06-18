<script setup lang="ts">
import type { PlanState } from '../plan/types'

defineProps<{ state: PlanState }>()
</script>

<template>
  <div v-if="!['idle','completed','cancelled','superseded'].includes(state)" class="plan-banner">
    <span class="banner-dot"></span>
    <div>
      <b>{{ state === 'awaiting_approval' ? '计划等待确认' : state === 'executing' ? '正在执行已批准计划' : '计划模式' }}</b>
      <span>{{ state === 'awaiting_approval' ? ' 可继续修改，或批准后执行。' : state === 'executing' ? ' 执行范围受批准计划约束。' : ' 当前只讨论和读取上下文，不执行修改。' }}</span>
    </div>
  </div>
</template>

<style scoped>
.plan-banner { margin: .7rem 3.2% 0; padding: .72rem .9rem; display: flex; gap: .65rem; align-items: center; border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border)); border-radius: 18px; background: linear-gradient(135deg, color-mix(in srgb, var(--accent-soft) 55%, transparent), color-mix(in srgb, var(--bg-card) 70%, transparent)); color: var(--fg-muted); box-shadow: var(--shadow-soft); }
.banner-dot { width: .7rem; height: .7rem; border-radius: 999px; background: var(--accent); box-shadow: 0 0 0 5px color-mix(in srgb, var(--accent) 15%, transparent); }
b { color: var(--fg); margin-right: .35rem; }
</style>
