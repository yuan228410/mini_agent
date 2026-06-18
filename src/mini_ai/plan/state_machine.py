from __future__ import annotations

from .schema import PlanState


TERMINAL_STATES: set[PlanState] = {"completed", "cancelled", "superseded"}

_ALLOWED: dict[PlanState, set[PlanState]] = {
    "idle": {"planning"},
    "planning": {"awaiting_user", "awaiting_approval", "cancelled", "superseded"},
    "awaiting_user": {"planning", "awaiting_approval", "cancelled", "superseded"},
    "awaiting_approval": {"planning", "approved", "cancelled", "superseded"},
    "approved": {"executing", "cancelled", "superseded"},
    "executing": {"completed", "cancelled"},
    "completed": {"superseded"},
    "cancelled": {"planning", "superseded"},
    "superseded": {"planning"},
}


class PlanTransitionError(ValueError):
    pass


def can_transition(current: PlanState, target: PlanState) -> bool:
    return target in _ALLOWED.get(current, set())


def ensure_transition(current: PlanState, target: PlanState) -> None:
    if not can_transition(current, target):
        raise PlanTransitionError(f"invalid plan transition: {current} -> {target}")


def is_planning_state(state: PlanState) -> bool:
    return state in {"planning", "awaiting_user", "awaiting_approval"}
