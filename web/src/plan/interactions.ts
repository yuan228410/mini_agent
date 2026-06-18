import type { PlanArtifact, PlanDecision, PlanInteraction } from './types'

export function isPlanDecisionResolved(decision: PlanDecision): boolean {
  return !!(decision.selected_option_ids?.length || decision.custom_value?.trim())
}

function recommendedIds(options: { id: string; recommended?: boolean }[] | undefined, allowMultiple = false): string[] {
  const ids = (options || []).filter(option => option.recommended).map(option => option.id)
  return allowMultiple ? ids : ids.slice(0, 1)
}

export function nextPlanInteraction(plan: PlanArtifact | null | undefined): PlanInteraction | null {
  if (!plan) return null
  if ((plan.options?.length || 0) > 1 && !plan.selected_option_id) {
    return {
      id: `${plan.plan_id}:${plan.revision}:top-option`,
      planId: plan.plan_id,
      revision: plan.revision,
      type: 'top_option',
      title: '先选择整体实施方案',
      description: plan.summary || plan.goal,
      options: plan.options,
      allowMultiple: false,
      selectedIds: recommendedIds(plan.options),
      customValue: '',
    }
  }

  for (const step of plan.steps || []) {
    for (const decision of step.decisions || []) {
      if (isPlanDecisionResolved(decision)) continue
      return {
        id: `${plan.plan_id}:${plan.revision}:decision:${step.id}:${decision.id}`,
        planId: plan.plan_id,
        revision: plan.revision,
        type: 'step_decision',
        title: decision.title,
        description: decision.description,
        options: decision.options || [],
        allowMultiple: !!decision.allow_multiple,
        selectedIds: decision.selected_option_ids?.length ? [...decision.selected_option_ids] : recommendedIds(decision.options, !!decision.allow_multiple),
        customValue: decision.custom_value || '',
        stepId: step.id,
        stepTitle: step.title,
        decisionId: decision.id,
      }
    }
  }

  return null
}

export function hasUnresolvedPlanInteractions(plan: PlanArtifact | null | undefined): boolean {
  return !!nextPlanInteraction(plan)
}

export function isFinalPlan(plan: PlanArtifact | null | undefined): boolean {
  return !!plan && !hasUnresolvedPlanInteractions(plan) && ['awaiting_approval', 'approved', 'executing', 'completed'].includes(plan.status)
}
