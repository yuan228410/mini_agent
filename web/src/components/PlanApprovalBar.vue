<script setup lang="ts">
import type { PlanArtifact } from '../plan/types'

defineProps<{ plan: PlanArtifact | null; visible: boolean; disabled?: boolean }>()
const emit = defineEmits<{ (e: 'approve'): void; (e: 'cancel'): void }>()
</script>

<template>
  <div v-if="visible && plan" class="approval-bar">
    <div>
      <div class="approval-title">计划 r{{ plan.revision }} 已准备执行</div>
      <div class="approval-sub">确认后才会启用写入/命令等执行能力。</div>
    </div>
    <div class="approval-actions">
      <button class="ghost" :disabled="disabled" @click="emit('cancel')">取消</button>
      <button class="primary" :disabled="disabled" @click="emit('approve')">批准并执行</button>
    </div>
  </div>
</template>

<style scoped>
.approval-bar { margin: 0 .9rem .6rem; padding: .78rem .9rem; border: 1px solid color-mix(in srgb, var(--accent) 42%, var(--border)); border-radius: 20px; background: linear-gradient(135deg, color-mix(in srgb, var(--bg-card) 88%, transparent), color-mix(in srgb, var(--accent-soft) 45%, transparent)); box-shadow: var(--shadow-soft); display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.approval-title { font-weight: 900; color: var(--fg); }
.approval-sub { color: var(--fg-muted); font-size: .82rem; margin-top: .15rem; }
.approval-actions { display: flex; gap: .5rem; }
button { border: 1px solid var(--surface-hairline); border-radius: 999px; padding: .45rem .75rem; font-weight: 900; cursor: pointer; }
.primary { background: var(--accent); color: var(--bg); border-color: var(--accent); }
.ghost { background: var(--surface-control); color: var(--fg-muted); }
button:disabled { opacity: .55; cursor: not-allowed; }
</style>
