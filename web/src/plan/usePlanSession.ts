import { computed, reactive } from 'vue'
import { hasUnresolvedPlanInteractions } from './interactions'
import type { PlanArtifact, PlanEventData, PlanState } from './types'

interface State {
  planState: PlanState
  currentPlan: PlanArtifact | null
  lastError: string
}

const state = reactive<State>({
  planState: 'idle',
  currentPlan: null,
  lastError: '',
})

export function usePlanSession() {
  const isPlanning = computed(() => ['planning', 'awaiting_user', 'awaiting_approval'].includes(state.planState))
  const awaitingApproval = computed(() => state.planState === 'awaiting_approval')
  const canApprove = computed(() => awaitingApproval.value && !!state.currentPlan)
  const inputMode = computed(() => {
    if (state.planState === 'executing') return 'executing'
    if (state.planState === 'awaiting_approval') return 'awaiting_approval'
    if (isPlanning.value) return 'planning'
    return 'chat'
  })

  function applyPlanEvent(data: PlanEventData) {
    if (data.error) state.lastError = data.error
    if (data.plan !== undefined) state.currentPlan = data.plan
    if (data.state) state.planState = data.state
    if (data.kind === 'artifact.updated' && data.plan) state.planState = data.plan.status
    if (data.kind === 'approval.required') state.planState = hasUnresolvedPlanInteractions(data.plan) ? 'awaiting_user' : 'awaiting_approval'
    if (data.kind === 'execution.started') state.planState = 'executing'
    if (data.kind === 'execution.completed') state.planState = 'completed'
    if (data.kind === 'cancelled') state.planState = 'cancelled'
    if (data.kind === 'approved') state.planState = 'approved'
  }

  function reset() {
    state.planState = 'idle'
    state.currentPlan = null
    state.lastError = ''
  }

  function setState(nextState: PlanState, currentPlan?: PlanArtifact | null) {
    state.planState = nextState
    if (currentPlan !== undefined) state.currentPlan = currentPlan
  }

  function restore(plan?: PlanArtifact | null) {
    if (!plan) {
      reset()
      return
    }
    state.currentPlan = plan
    state.planState = plan.status
  }

  return {
    state,
    isPlanning,
    awaitingApproval,
    canApprove,
    inputMode,
    applyPlanEvent,
    setState,
    reset,
    restore,
  }
}
