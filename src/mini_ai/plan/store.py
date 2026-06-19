from __future__ import annotations

from ..core.runtime_types import HistoryDBProtocol, PlanStateValue
from .schema import PlanArtifact, PlanArtifactDict


class PlanStore:
    def __init__(self, history_db: HistoryDBProtocol, workspace: str, session_id: str):
        self.history_db = history_db
        self.workspace = workspace
        self.session_id = session_id

    def save(self, artifact: PlanArtifact) -> None:
        self.history_db.save_plan(self.workspace, self.session_id, artifact.to_dict())

    def current(self) -> PlanArtifactDict | None:
        return self.history_db.get_current_plan(self.workspace, self.session_id)

    def list(self) -> list[PlanArtifactDict]:
        return self.history_db.list_plans(self.workspace, self.session_id)

    def mark_status(self, plan_id: str, status: PlanStateValue) -> None:
        self.history_db.mark_plan_status(self.workspace, self.session_id, plan_id, status)
