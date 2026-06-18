export type PlanState =
  | 'idle'
  | 'planning'
  | 'awaiting_user'
  | 'awaiting_approval'
  | 'approved'
  | 'executing'
  | 'completed'
  | 'cancelled'
  | 'superseded'

export interface PlanOption {
  id: string
  title: string
  summary: string
  pros: string[]
  cons: string[]
  risk_level: 'low' | 'medium' | 'high'
  estimated_effort?: string
  recommended?: boolean
}

export interface PlanDecisionOption {
  id: string
  title: string
  summary?: string
  recommended?: boolean
}

export interface PlanDecision {
  id: string
  title: string
  description?: string
  allow_multiple?: boolean
  options: PlanDecisionOption[]
  selected_option_ids?: string[]
  custom_value?: string
}

export interface PlanStep {
  id: string
  title: string
  description: string
  files: string[]
  validation: string[]
  depends_on: string[]
  decisions?: PlanDecision[]
}

export interface PlanArtifact {
  plan_id: string
  revision: number
  status: PlanState
  goal: string
  summary: string
  assumptions: string[]
  open_questions: string[]
  options: PlanOption[]
  selected_option_id?: string | null
  steps: PlanStep[]
  risks: string[]
  validation_strategy: string[]
  created_at: string
  updated_at: string
}

export interface PlanInteractionOption {
  id: string
  title: string
  summary?: string
  pros?: string[]
  cons?: string[]
  risk_level?: 'low' | 'medium' | 'high'
  estimated_effort?: string
  recommended?: boolean
}

export interface PlanInteraction {
  id: string
  planId: string
  revision: number
  type: 'top_option' | 'step_decision'
  title: string
  description?: string
  options: PlanInteractionOption[]
  allowMultiple: boolean
  selectedIds: string[]
  customValue?: string
  stepId?: string
  stepTitle?: string
  decisionId?: string
  completed?: boolean
}

export interface PlanEventData {
  kind: string
  state?: PlanState
  mode?: 'plan' | 'chat' | 'execute'
  plan?: PlanArtifact | null
  plan_id?: string
  revision?: number
  option_id?: string
  questions?: string[]
  error?: string
}
