from __future__ import annotations

from ..utils import now_ts
from .artifact_parser import parse_plan_artifact, strip_artifact_blocks
from .prompts import build_execution_instruction
from .schema import PlanArtifact, PlanSessionState, new_plan_id
from .state_machine import ensure_transition


class PlanService:
    """共享计划模式服务。Web/CLI 只负责传输与展示，状态与产物在这里收敛。"""

    def start(self, *, session_key: str, sm, store, goal: str = "", display=None) -> PlanArtifact:
        previous = sm.get_plan_state(session_key)
        if previous.state not in ("idle", "completed", "cancelled", "superseded"):
            if previous.current_plan:
                old = dict(previous.current_plan)
                old["status"] = "superseded"
                old_artifact = PlanArtifact.from_dict(old)
                if old_artifact:
                    store.save(old_artifact)

        artifact = PlanArtifact(
            plan_id=new_plan_id(),
            revision=1,
            status="planning",
            goal=goal or "待明确",
            summary="计划模式已启动。请继续描述目标、约束或偏好的方案。",
        )
        store.save(artifact)
        sm.set_plan_state(session_key, PlanSessionState(
            state="planning",
            current_plan=artifact.to_dict(),
            selected_option_id=artifact.selected_option_id,
            updated_at=now_ts(),
        ))
        self._emit(display, "state.changed", state="planning", mode="plan")
        self._emit(display, "artifact.updated", plan=artifact.to_dict())
        return artifact

    def update_from_response(self, *, session_key: str, sm, store, user_text: str, assistant_text: str, display=None) -> PlanArtifact:
        plan_state = sm.get_plan_state(session_key)
        current = plan_state.current_plan or store.current()
        artifact = parse_plan_artifact(assistant_text, previous=current, goal=user_text)
        if artifact is None:
            artifact = self._fallback_artifact(current, user_text, assistant_text)

        store.save(artifact)
        sm.set_plan_state(session_key, PlanSessionState(
            state=artifact.status,
            current_plan=artifact.to_dict(),
            selected_option_id=artifact.selected_option_id,
            updated_at=now_ts(),
        ))
        self._emit(display, "state.changed", state=artifact.status, mode="plan")
        self._emit(display, "artifact.updated", plan=artifact.to_dict())
        if artifact.status == "awaiting_user":
            self._emit(display, "question.required", plan_id=artifact.plan_id, revision=artifact.revision, questions=artifact.open_questions)
        elif artifact.status == "awaiting_approval":
            self._emit(display, "approval.required", plan=artifact.to_dict(), plan_id=artifact.plan_id, revision=artifact.revision)
        return artifact

    def select_option(self, *, session_key: str, sm, store, plan_id: str, option_id: str, display=None) -> PlanArtifact:
        state = sm.get_plan_state(session_key)
        artifact = PlanArtifact.from_dict(state.current_plan or store.current())
        if artifact is None or artifact.plan_id != plan_id:
            raise ValueError("计划不存在或已过期")
        artifact.selected_option_id = option_id
        artifact.revision += 1
        artifact.updated_at = now_ts()
        artifact.status = "awaiting_user" if self._has_unresolved_interactions(artifact) else "awaiting_approval"
        store.save(artifact)
        sm.set_plan_state(session_key, PlanSessionState(
            state=artifact.status,
            current_plan=artifact.to_dict(),
            selected_option_id=option_id,
            updated_at=now_ts(),
        ))
        self._emit(display, "option.selected", plan=artifact.to_dict(), option_id=option_id)
        if artifact.status == "awaiting_approval":
            self._emit(display, "approval.required", plan=artifact.to_dict(), plan_id=artifact.plan_id, revision=artifact.revision)
        return artifact

    def apply_decision(self, *, session_key: str, sm, store, plan_id: str, step_id: str, decision_id: str,
                       selected_option_ids: list[str] | None = None, custom_value: str = "", revision: int | None = None,
                       display=None) -> PlanArtifact:
        state = sm.get_plan_state(session_key)
        artifact = PlanArtifact.from_dict(state.current_plan or store.current())
        if artifact is None or artifact.plan_id != plan_id:
            raise ValueError("计划不存在或已过期")
        if revision is not None and artifact.revision != revision:
            raise ValueError("计划已更新，请基于最新版本选择")

        target = None
        for step in artifact.steps:
            if step.id != step_id:
                continue
            for decision in step.decisions:
                if decision.id == decision_id:
                    target = decision
                    break
            break
        if target is None:
            raise ValueError("未找到对应的步骤决策")

        selected = [str(v) for v in (selected_option_ids or []) if str(v).strip()]
        valid_ids = {option.id for option in target.options}
        if selected and valid_ids:
            unknown = [v for v in selected if v not in valid_ids]
            if unknown:
                raise ValueError(f"未知决策选项: {', '.join(unknown)}")
        if not target.allow_multiple and len(selected) > 1:
            raise ValueError("该决策只允许单选")
        if not selected and not custom_value.strip():
            raise ValueError("请选择选项或填写其他想法")

        target.selected_option_ids = selected
        target.custom_value = custom_value.strip()
        artifact.revision += 1
        artifact.updated_at = now_ts()
        artifact.status = "awaiting_user" if self._has_unresolved_interactions(artifact) else "awaiting_approval"
        store.save(artifact)
        sm.set_plan_state(session_key, PlanSessionState(
            state=artifact.status,
            current_plan=artifact.to_dict(),
            selected_option_id=artifact.selected_option_id,
            updated_at=now_ts(),
        ))
        self._emit(display, "decision.applied", plan=artifact.to_dict(), step_id=step_id, decision_id=decision_id)
        self._emit(display, "artifact.updated", plan=artifact.to_dict())
        if artifact.status == "awaiting_approval":
            self._emit(display, "approval.required", plan=artifact.to_dict(), plan_id=artifact.plan_id, revision=artifact.revision)
        return artifact

    def approve(self, *, session_key: str, sm, store, plan_id: str, revision: int, display=None) -> PlanArtifact:
        state = sm.get_plan_state(session_key)
        artifact = PlanArtifact.from_dict(state.current_plan or store.current())
        if artifact is None:
            raise ValueError("没有可审批的计划")
        if artifact.plan_id != plan_id or artifact.revision != revision:
            raise ValueError("计划已更新，请审批最新版本")
        ensure_transition(artifact.status, "approved")
        artifact.status = "approved"
        artifact.updated_at = now_ts()
        store.save(artifact)
        sm.set_plan_state(session_key, PlanSessionState(
            state="approved",
            current_plan=artifact.to_dict(),
            approved_plan=artifact.to_dict(),
            selected_option_id=artifact.selected_option_id,
            updated_at=now_ts(),
        ))
        self._emit(display, "approved", plan=artifact.to_dict())
        return artifact

    def mark_executing(self, *, session_key: str, sm, store, artifact: PlanArtifact, display=None) -> PlanArtifact:
        base_artifact = PlanArtifact.from_dict(artifact.to_dict()) or artifact
        artifact.status = "executing"
        artifact.updated_at = now_ts()
        store.save(artifact)
        sm.set_plan_state(session_key, PlanSessionState(
            state="executing",
            current_plan=artifact.to_dict(),
            approved_plan=base_artifact.to_dict(),
            selected_option_id=artifact.selected_option_id,
            updated_at=now_ts(),
        ))
        self._emit(display, "execution.started", plan=artifact.to_dict(), mode="execute")
        return artifact

    def mark_completed(self, *, session_key: str, sm, store, display=None) -> None:
        state = sm.get_plan_state(session_key)
        artifact = PlanArtifact.from_dict(state.approved_plan or state.current_plan)
        current = artifact.to_dict() if artifact else None
        if artifact:
            stored = PlanArtifact.from_dict(artifact.to_dict())
            if stored:
                stored.status = "completed"
                stored.updated_at = now_ts()
                store.save(stored)
        sm.set_plan_state(session_key, PlanSessionState(state="completed", current_plan=current, approved_plan=current, updated_at=now_ts()))
        self._emit(display, "execution.completed", plan=current, mode="chat")

    def cancel(self, *, session_key: str, sm, store, display=None) -> None:
        state = sm.get_plan_state(session_key)
        artifact = PlanArtifact.from_dict(state.current_plan or store.current())
        if artifact:
            artifact.status = "cancelled"
            artifact.updated_at = now_ts()
            store.save(artifact)
        sm.set_plan_state(session_key, PlanSessionState(state="cancelled", current_plan=artifact.to_dict() if artifact else None, updated_at=now_ts()))
        self._emit(display, "cancelled", plan=artifact.to_dict() if artifact else None, mode="chat")

    def execution_instruction(self, artifact: PlanArtifact | dict) -> str:
        return build_execution_instruction(artifact)

    def seed_execution_todos(self, *, artifact: PlanArtifact | dict, session_key: str, display=None) -> str:
        payload = artifact.to_dict() if isinstance(artifact, PlanArtifact) else artifact
        steps = payload.get("steps") or []
        todos = [
            {"id": idx, "content": str(step.get("title") or f"执行步骤 {idx}"), "status": "pending"}
            for idx, step in enumerate(steps, start=1)
            if isinstance(step, dict)
        ]
        if not todos:
            return ""
        from ..tools.update_todos import set_todos
        result = set_todos(session_key, todos)
        if display and hasattr(display, "tool_result") and result:
            display.tool_result("update_todos", result, 0, "")
        return result

    def _has_unresolved_interactions(self, artifact: PlanArtifact) -> bool:
        if len(artifact.options) > 1 and not artifact.selected_option_id:
            return True
        return any(
            decision.options and not decision.selected_option_ids and not decision.custom_value.strip()
            for step in artifact.steps
            for decision in step.decisions
        )

    def _fallback_artifact(self, current: dict | None, user_text: str, assistant_text: str) -> PlanArtifact:
        previous = current or {}
        return PlanArtifact(
            plan_id=previous.get("plan_id") or new_plan_id(),
            revision=int(previous.get("revision") or 0) + 1,
            status="awaiting_approval",
            goal=previous.get("goal") or user_text,
            summary=(strip_artifact_blocks(assistant_text) or "已生成计划，请确认是否执行。").strip()[:1200],
            assumptions=previous.get("assumptions") or [],
            open_questions=[],
            selected_option_id=previous.get("selected_option_id"),
            created_at=previous.get("created_at") or now_ts(),
            updated_at=now_ts(),
        )

    def _emit(self, display, kind: str, **data) -> None:
        if display and hasattr(display, "plan_event"):
            display.plan_event(kind, **data)
