"""WebSocket chat run coordinator."""
from __future__ import annotations

from ..core.runtime_types import PlanArtifactDict
from ..logger import logger
from .chat_command_dispatch import error_event
from .chat_endpoint_state import ChatEndpointState
from .chat_events import relay_chat_queue_events
from .chat_executor import launch_chat_executor
from .chat_run_context import prepare_chat_run_context
from .chat_run_finalization import finalize_chat_run
from .route_types import ImageUpload


async def run_chat_websocket_turn(
    endpoint_state: ChatEndpointState,
    *,
    sid: str,
    username: str,
    user_message: str,
    workspace: str | None = None,
    images: list[ImageUpload] | None = None,
    plan_turn: bool = False,
    approved_plan: PlanArtifactDict | None = None,
) -> None:
    """Run one chat WebSocket turn from context creation through finalization."""

    logger.info(
        f"[Web] WS _run_chat sid={sid} user={username} ws={workspace} "
        f"images={len(images) if images else 0} plan_turn={plan_turn} approved={bool(approved_plan)}"
    )
    run_context = prepare_chat_run_context(
        endpoint_state.session_dependencies,
        username=username,
        session_id=sid,
        workspace=workspace,
        user_message=user_message,
        images=images,
    )
    endpoint_state.abort_keys.append(run_context.session_key)

    future = launch_chat_executor(
        run_context,
        username=username,
        workspace=workspace,
        plan_turn=plan_turn,
        approved_plan=approved_plan,
    )

    try:
        relay_result = await relay_chat_queue_events(
            queue=run_context.queue,
            abort_event=run_context.abort_event,
            future=future,
            session_id=sid,
            send=endpoint_state.send,
        )
    except Exception as exc:
        logger.error(f"[Web] WS chat task error: {exc}", exc_info=True)
        await endpoint_state.send(error_event(str(exc), sid))
        relay_result = None

    await finalize_chat_run(
        run_context=run_context,
        future=future,
        session_manager=endpoint_state.session_manager,
        update_meta_cache=endpoint_state.update_meta_cache,
        send=endpoint_state.send,
        sid=sid,
        username=username,
        workspace=workspace,
        usage=relay_result.usage if relay_result else {"prompt_tokens": 0, "completion_tokens": 0},
        aborted=relay_result.aborted if relay_result else False,
        got_terminal=relay_result.got_terminal if relay_result else False,
    )
