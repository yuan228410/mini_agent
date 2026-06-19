from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, TypedDict
import uuid

from ..core.runtime_types import PlanArtifactDict as PlanArtifactDict
from ..utils import now_ts


PlanState = Literal[
    "idle",
    "planning",
    "awaiting_user",
    "awaiting_approval",
    "approved",
    "executing",
    "completed",
    "cancelled",
    "superseded",
]

class PlanSessionStateDict(TypedDict):
    state: PlanState
    current_plan: PlanArtifactDict | None
    approved_plan: PlanArtifactDict | None
    selected_option_id: str | None
    updated_at: str


@dataclass
class PlanOption:
    id: str
    title: str
    summary: str
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    estimated_effort: str = ""
    recommended: bool = False


@dataclass
class PlanDecisionOption:
    id: str
    title: str
    summary: str = ""
    recommended: bool = False


@dataclass
class PlanDecision:
    id: str
    title: str
    description: str = ""
    allow_multiple: bool = False
    options: list[PlanDecisionOption] = field(default_factory=list)
    selected_option_ids: list[str] = field(default_factory=list)
    custom_value: str = ""


@dataclass
class PlanStep:
    id: str
    title: str
    description: str
    files: list[str] = field(default_factory=list)
    validation: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    decisions: list[PlanDecision] = field(default_factory=list)


@dataclass
class PlanArtifact:
    plan_id: str
    revision: int
    status: PlanState
    goal: str
    summary: str
    assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    options: list[PlanOption] = field(default_factory=list)
    selected_option_id: str | None = None
    steps: list[PlanStep] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    validation_strategy: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_ts)
    updated_at: str = field(default_factory=now_ts)

    def to_dict(self) -> PlanArtifactDict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: PlanArtifactDict | None) -> "PlanArtifact | None":
        if not data:
            return None
        field_names = set(cls.__dataclass_fields__.keys())
        payload = {k: v for k, v in data.items() if k in field_names}
        option_fields = set(PlanOption.__dataclass_fields__.keys())
        step_fields = set(PlanStep.__dataclass_fields__.keys())
        decision_fields = set(PlanDecision.__dataclass_fields__.keys())
        decision_option_fields = set(PlanDecisionOption.__dataclass_fields__.keys())
        payload["options"] = [PlanOption(**{k: v for k, v in o.items() if k in option_fields}) for o in data.get("options", []) if isinstance(o, dict)]
        steps = []
        for raw_step in data.get("steps", []):
            if not isinstance(raw_step, dict):
                continue
            step_payload = {k: v for k, v in raw_step.items() if k in step_fields}
            decisions = []
            for raw_decision in raw_step.get("decisions", []):
                if not isinstance(raw_decision, dict):
                    continue
                decision_payload = {k: v for k, v in raw_decision.items() if k in decision_fields}
                decision_payload["options"] = [
                    PlanDecisionOption(**{k: v for k, v in raw_choice.items() if k in decision_option_fields})
                    for raw_choice in raw_decision.get("options", [])
                    if isinstance(raw_choice, dict)
                ]
                decisions.append(PlanDecision(**decision_payload))
            step_payload["decisions"] = decisions
            steps.append(PlanStep(**step_payload))
        payload["steps"] = steps
        return cls(**payload)


@dataclass
class PlanSessionState:
    state: PlanState = "idle"
    current_plan: PlanArtifactDict | None = None
    approved_plan: PlanArtifactDict | None = None
    selected_option_id: str | None = None
    updated_at: str = field(default_factory=now_ts)

    def to_dict(self) -> PlanSessionStateDict:
        return asdict(self)

    @property
    def is_active(self) -> bool:
        return self.state in {"planning", "awaiting_user", "awaiting_approval", "approved", "executing"}


def new_plan_id() -> str:
    return f"plan-{uuid.uuid4().hex[:10]}"
