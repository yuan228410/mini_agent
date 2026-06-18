<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import type { PlanOption } from '../plan/types'

const props = defineProps<{
  visible: boolean
  mode: 'option' | 'decision'
  title: string
  subtitle?: string
  options: Array<PlanOption | { id: string; title: string; summary?: string; recommended?: boolean }>
  allowMultiple?: boolean
  selectedIds?: string[]
  customValue?: string
  stepTitle?: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'confirm', payload: { selectedIds: string[]; customValue: string }): void
}>()

const local = reactive({ selectedIds: [] as string[], customValue: '' })

watch(
  () => [props.visible, props.selectedIds, props.customValue] as const,
  () => {
    if (!props.visible) return
    local.selectedIds = [...(props.selectedIds || [])]
    local.customValue = props.customValue || ''
  },
  { immediate: true }
)

const modeLabel = computed(() => {
  if (props.mode === 'option') return '方案确认'
  return props.allowMultiple ? '多选决策' : '单选决策'
})

const canConfirm = computed(() => local.selectedIds.length > 0 || local.customValue.trim().length > 0)

function riskLevel(option: any): string {
  return option.risk_level || ''
}

function toggle(id: string) {
  if (props.allowMultiple) {
    local.selectedIds = local.selectedIds.includes(id)
      ? local.selectedIds.filter(v => v !== id)
      : [...local.selectedIds, id]
  } else {
    local.selectedIds = [id]
  }
}

function confirm() {
  if (!canConfirm.value) return
  emit('confirm', {
    selectedIds: [...local.selectedIds],
    customValue: local.customValue.trim(),
  })
}
</script>

<template>
  <Teleport to="body">
    <Transition name="plan-dialog-fade">
      <div v-if="visible" class="dialog-backdrop" @click.self="emit('close')">
        <section class="choice-dialog" role="dialog" aria-modal="true" :aria-label="title">
          <div class="dialog-orbit orbit-a"></div>
          <div class="dialog-orbit orbit-b"></div>

          <header class="dialog-head">
            <div>
              <div class="eyebrow">{{ modeLabel }}</div>
              <h3>{{ title }}</h3>
              <p v-if="subtitle">{{ subtitle }}</p>
              <p v-if="stepTitle" class="step-context">步骤：{{ stepTitle }}</p>
            </div>
            <button class="icon-btn" title="关闭" @click="emit('close')">×</button>
          </header>

          <div class="dialog-body">
            <div class="choice-grid">
              <button
                v-for="option in options"
                :key="option.id"
                class="choice-tile"
                :class="{
                  active: local.selectedIds.includes(option.id),
                  recommended: option.recommended,
                  [`risk-${riskLevel(option)}`]: !!riskLevel(option),
                }"
                @click="toggle(option.id)"
              >
                <span class="select-dot">{{ local.selectedIds.includes(option.id) ? '✓' : '' }}</span>
                <span class="choice-main">
                  <span class="choice-title">{{ option.title }}</span>
                  <span v-if="option.recommended" class="badge">推荐</span>
                  <span v-if="riskLevel(option)" class="risk">{{ riskLevel(option) }}</span>
                  <span v-if="option.summary" class="choice-summary">{{ option.summary }}</span>
                </span>
              </button>
            </div>

            <label class="custom-box">
              <span>都不满意？写下你的想法</span>
              <textarea
                v-model="local.customValue"
                rows="4"
                placeholder="例如：我倾向另一个折中方案 / 这一步请改用 xxx / 需要同时保留两个方向…"
                @keydown.meta.enter.prevent="confirm"
                @keydown.ctrl.enter.prevent="confirm"
              ></textarea>
            </label>
          </div>

          <footer class="dialog-actions">
            <button class="ghost" @click="emit('close')">取消</button>
            <button class="primary" :disabled="!canConfirm" @click="confirm">
              {{ mode === 'option' && !local.customValue.trim() ? '确认选择方案' : '确认并修订计划' }}
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 4000;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  background: radial-gradient(circle at 50% 16%, color-mix(in srgb, var(--accent-glow) 22%, transparent), transparent 42%), rgba(4, 7, 10, .58);
  backdrop-filter: blur(16px) saturate(1.08);
}

.choice-dialog {
  position: relative;
  width: min(760px, 96vw);
  max-height: min(82vh, 860px);
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--accent) 34%, var(--border));
  border-radius: 28px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--bg-elevated) 96%, transparent), color-mix(in srgb, var(--bg-card) 94%, transparent));
  box-shadow: 0 28px 90px rgba(0,0,0,.38), var(--glow-accent);
}

.dialog-orbit {
  position: absolute;
  pointer-events: none;
  border: 1px solid color-mix(in srgb, var(--accent) 18%, transparent);
  border-radius: 999px;
  opacity: .45;
}
.orbit-a { width: 260px; height: 260px; right: -120px; top: -120px; }
.orbit-b { width: 180px; height: 180px; left: -92px; bottom: -96px; }

.dialog-head,
.dialog-actions {
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.15rem;
}

.dialog-head {
  align-items: flex-start;
  border-bottom: 1px solid var(--surface-hairline);
  background: linear-gradient(180deg, color-mix(in srgb, var(--accent-soft) 26%, transparent), transparent);
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
  font-size: 1.12rem;
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

.icon-btn {
  width: 34px;
  height: 34px;
  border: 1px solid var(--surface-hairline);
  border-radius: 999px;
  background: var(--surface-control);
  color: var(--fg-muted);
  font-size: 1.2rem;
  cursor: pointer;
}
.icon-btn:hover { color: var(--accent); border-color: var(--accent); }

.dialog-body {
  position: relative;
  z-index: 1;
  max-height: calc(min(82vh, 860px) - 168px);
  overflow: auto;
  padding: 1rem 1.15rem;
}

.choice-grid { display: grid; gap: .65rem; }

.choice-tile {
  display: grid;
  grid-template-columns: 1.6rem 1fr;
  gap: .7rem;
  width: 100%;
  text-align: left;
  padding: .82rem;
  border: 1px solid var(--surface-hairline);
  border-radius: 18px;
  background: color-mix(in srgb, var(--bg-card) 78%, transparent);
  color: var(--fg);
  cursor: pointer;
  transition: transform .16s var(--ease-out), border-color .16s var(--ease-out), background .16s var(--ease-out);
}
.choice-tile:hover { transform: translateY(-1px); border-color: color-mix(in srgb, var(--accent) 46%, var(--border)); }
.choice-tile.active { border-color: var(--accent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 24%, transparent); }
.choice-tile.recommended { background: linear-gradient(135deg, color-mix(in srgb, var(--accent-soft) 42%, transparent), color-mix(in srgb, var(--bg-card) 78%, transparent)); }

.select-dot {
  width: 1.35rem;
  height: 1.35rem;
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
.choice-summary { display: block; margin-top: .25rem; color: var(--fg-muted); line-height: 1.48; }

.custom-box {
  display: grid;
  gap: .45rem;
  margin-top: .9rem;
  color: var(--fg-muted);
  font-size: .82rem;
  font-weight: 800;
}
.custom-box textarea {
  resize: vertical;
  min-height: 92px;
  border: 1px solid var(--surface-hairline);
  border-radius: 18px;
  background: var(--bg-input);
  color: var(--fg);
  padding: .78rem .85rem;
  outline: none;
  line-height: 1.55;
}
.custom-box textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-soft) 42%, transparent); }

.dialog-actions {
  align-items: center;
  border-top: 1px solid var(--surface-hairline);
  background: color-mix(in srgb, var(--bg-card) 72%, transparent);
}
.dialog-actions button {
  border-radius: 999px;
  padding: .55rem .9rem;
  font-weight: 900;
  cursor: pointer;
}
.ghost { border: 1px solid var(--surface-hairline); background: var(--surface-control); color: var(--fg-muted); }
.primary { border: 1px solid var(--accent); background: var(--accent); color: var(--accent-ink); }
.primary:disabled { opacity: .48; cursor: not-allowed; }

.plan-dialog-fade-enter-active,
.plan-dialog-fade-leave-active { transition: opacity .18s var(--ease-out); }
.plan-dialog-fade-enter-from,
.plan-dialog-fade-leave-to { opacity: 0; }
.plan-dialog-fade-enter-active .choice-dialog { animation: popIn .2s var(--ease-out); }

@keyframes popIn {
  from { transform: translateY(10px) scale(.98); opacity: .76; }
  to { transform: translateY(0) scale(1); opacity: 1; }
}
</style>
