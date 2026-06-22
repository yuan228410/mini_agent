"""WebSocket chat executor submission helpers."""
from __future__ import annotations

from typing import Any

from ..core.runtime_types import PlanArtifactDict
from .chat_run_context import ChatRunContext
from .chat_runner import _executor, run_tool_loop_sync


def launch_chat_executor(
    run_context: ChatRunContext,
    *,
    username: str,
    workspace: str | None,
    plan_turn: bool,
    approved_plan: PlanArtifactDict | None,
    executor: Any = _executor,
    runner: Any = run_tool_loop_sync,
):
    """Submit the synchronous chat runner to the Web chat executor."""

    return run_context.loop.run_in_executor(
        executor,
        runner,
        *run_context.executor_args(
            username=username,
            workspace=workspace,
            plan_turn=plan_turn,
            approved_plan=approved_plan,
        ),
    )
