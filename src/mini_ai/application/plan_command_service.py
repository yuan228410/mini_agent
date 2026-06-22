"""Application-level plan command handling for chat adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.events import DisplayEvent, DisplayEventType
from ..core.runtime_types import DisplayWireEvent, MessageDict, PlanArtifactDict
from ..plan.service import PlanService
from ..plan.store import PlanStore


@dataclass(frozen=True, slots=True)
class PlanCommandDependencies:
    session_manager: Any
    cache_key: Callable[[str, str | None, str], str]
    resolve_base: Callable[[str, str | None], Path]
    get_or_create_session: Callable[..., tuple[str, list[MessageDict] | None]]
    get_or_create_components: Callable[[str, str, Path | None, str | None], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PlanRunRequest:
    sid: str
    username: str
    user_message: str
    workspace: str | None = None
    images: list[dict[str, Any]] | None = None
    plan_turn: bool = False
    approved_plan: PlanArtifactDict | None = None


@dataclass(frozen=True, slots=True)
class PlanCommandResult:
    events: list[DisplayWireEvent] = field(default_factory=list)
    sid: str | None = None
    session_key: str | None = None
    run: PlanRunRequest | None = None
    handled: bool = True


def _plan_event(kind: str, session_id: str | None = None, **data) -> DisplayWireEvent:
    payload = {"kind": kind}
    payload.update(data)
    if session_id:
        payload["session_id"] = session_id
    return DisplayEvent(DisplayEventType.PLAN_EVENT, payload).to_wire()


def _error_event(error: str, session_id: str | None = None) -> DisplayWireEvent:
    payload = {"error": error}
    if session_id:
        payload["session_id"] = session_id
    return DisplayEvent(DisplayEventType.ERROR, payload).to_wire()


def _session_context(deps: PlanCommandDependencies, *, username: str, session_id: str, workspace: str | None) -> tuple[str, str, PlanStore]:
    base = deps.resolve_base(username, workspace)
    sid, _messages = deps.get_or_create_session(username, session_id, base, workspace)
    comp = deps.get_or_create_components(username, sid, base, workspace)
    session_key = deps.cache_key(username, workspace, sid)
    store = PlanStore(comp["history_db"], workspace or "default", sid)
    return sid, session_key, store


def handle_plan_command(
    deps: PlanCommandDependencies,
    *,
    username: str,
    session_id: str | None,
    workspace: str | None,
    msg_type: str,
    payload: dict[str, Any],
) -> PlanCommandResult:
    """Handle a WebSocket plan.* command without binding to WebSocket I/O."""

    if not msg_type.startswith("plan."):
        return PlanCommandResult(handled=False)
    if not session_id:
        return PlanCommandResult(events=[_error_event("请先选择会话")])

    sid, session_key, store = _session_context(deps, username=username, session_id=session_id, workspace=workspace)
    plan_svc = PlanService()

    try:
        if msg_type == "plan.start":
            artifact = plan_svc.start(session_key=session_key, sm=deps.session_manager, store=store, goal=payload.get("goal", ""))
            return PlanCommandResult(
                sid=sid,
                session_key=session_key,
                events=[
                    _plan_event("state.changed", sid, state=artifact.status, mode="plan"),
                    _plan_event("artifact.updated", sid, plan=artifact.to_dict()),
                ],
            )

        if msg_type == "plan.select_option":
            artifact = plan_svc.select_option(
                session_key=session_key,
                sm=deps.session_manager,
                store=store,
                plan_id=payload.get("plan_id", ""),
                option_id=payload.get("option_id", ""),
            )
            return PlanCommandResult(
                sid=sid,
                session_key=session_key,
                events=[
                    _plan_event("option.selected", sid, option_id=artifact.selected_option_id, plan=artifact.to_dict()),
                    _plan_event("approval.required", sid, plan=artifact.to_dict(), plan_id=artifact.plan_id, revision=artifact.revision),
                ],
            )

        if msg_type == "plan.apply_decision":
            artifact = plan_svc.apply_decision(
                session_key=session_key,
                sm=deps.session_manager,
                store=store,
                plan_id=payload.get("plan_id", ""),
                step_id=payload.get("step_id", ""),
                decision_id=payload.get("decision_id", ""),
                selected_option_ids=payload.get("selected_option_ids") or [],
                custom_value=payload.get("custom_value", ""),
                revision=int(payload["revision"]) if payload.get("revision") is not None else None,
            )
            events = [
                _plan_event("decision.applied", sid, plan=artifact.to_dict(), step_id=payload.get("step_id", ""), decision_id=payload.get("decision_id", "")),
                _plan_event("artifact.updated", sid, plan=artifact.to_dict()),
            ]
            if artifact.status == "awaiting_approval":
                events.append(_plan_event("approval.required", sid, plan=artifact.to_dict(), plan_id=artifact.plan_id, revision=artifact.revision))
            return PlanCommandResult(sid=sid, session_key=session_key, events=events)

        if msg_type == "plan.cancel":
            plan_svc.cancel(session_key=session_key, sm=deps.session_manager, store=store)
            return PlanCommandResult(sid=sid, session_key=session_key, events=[_plan_event("cancelled", sid, mode="chat")])

        if msg_type == "plan.approve":
            return _approve_plan(
                deps,
                plan_svc=plan_svc,
                store=store,
                sid=sid,
                session_key=session_key,
                username=username,
                workspace=workspace,
                plan_id=payload.get("plan_id", ""),
                revision=int(payload.get("revision") or 0),
            )

        if msg_type in ("plan.message", "plan.revise"):
            user_message = payload.get("message", "").strip()
            if not user_message:
                return PlanCommandResult(sid=sid, session_key=session_key, events=[_error_event("计划消息不能为空", sid)])
            return PlanCommandResult(
                sid=sid,
                session_key=session_key,
                run=PlanRunRequest(sid=sid, username=username, user_message=user_message, workspace=workspace, images=payload.get("images"), plan_turn=True),
            )

        return PlanCommandResult(sid=sid, session_key=session_key, handled=False)
    except Exception as exc:
        return PlanCommandResult(sid=sid, session_key=session_key, events=[_plan_event("error", sid, error=str(exc))])


def approve_current_plan(
    deps: PlanCommandDependencies,
    *,
    username: str,
    session_id: str | None,
    workspace: str | None,
) -> PlanCommandResult:
    """Approve the current plan artifact and request execution."""

    if not session_id:
        return PlanCommandResult(events=[_error_event("请先选择会话")])

    sid, session_key, store = _session_context(deps, username=username, session_id=session_id, workspace=workspace)
    artifact_dict = deps.session_manager.get_plan_state(session_key).current_plan or store.current()
    if not artifact_dict:
        return PlanCommandResult(sid=sid, session_key=session_key, events=[_plan_event("error", sid, error="当前没有可审批的计划")])

    try:
        return _approve_plan(
            deps,
            plan_svc=PlanService(),
            store=store,
            sid=sid,
            session_key=session_key,
            username=username,
            workspace=workspace,
            plan_id=artifact_dict.get("plan_id", ""),
            revision=int(artifact_dict.get("revision") or 0),
        )
    except Exception as exc:
        return PlanCommandResult(sid=sid, session_key=session_key, events=[_plan_event("error", sid, error=str(exc))])


def _approve_plan(
    deps: PlanCommandDependencies,
    *,
    plan_svc: PlanService,
    store: PlanStore,
    sid: str,
    session_key: str,
    username: str,
    workspace: str | None,
    plan_id: str,
    revision: int,
) -> PlanCommandResult:
    artifact = plan_svc.approve(session_key=session_key, sm=deps.session_manager, store=store, plan_id=plan_id, revision=revision)
    executing_artifact = plan_svc.mark_executing(session_key=session_key, sm=deps.session_manager, store=store, artifact=artifact)
    return PlanCommandResult(
        sid=sid,
        session_key=session_key,
        events=[
            _plan_event("approved", sid, plan=artifact.to_dict()),
            _plan_event("execution.started", sid, plan=executing_artifact.to_dict(), mode="execute"),
        ],
        run=PlanRunRequest(sid=sid, username=username, user_message="", workspace=workspace, plan_turn=False, approved_plan=executing_artifact.to_dict()),
    )
