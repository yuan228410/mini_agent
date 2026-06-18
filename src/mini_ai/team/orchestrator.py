"""DAG 驱动的多 agent 编排器"""
import contextvars
import threading
import time

from .blackboard import Blackboard
from ..config import TEAMMATE
from ..logger import logger
from .prompts import build_team_prompt
from .task_graph import TaskGraph, TaskNode, TaskStatus
from ..utils import now_ts

class Orchestrator:

    def __init__(self, graph: TaskGraph, blackboard: Blackboard, *, context_length: int = 256000, bus=None, manager=None, display=None):
        self.graph = graph
        self.blackboard = blackboard
        self.context_length = context_length
        self.bus = bus
        self.manager = manager
        self._display = display
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._pending_results: dict[str, tuple[str | None, str | None]] = {}

    def _push_event(self, event: str, data: dict):
        """通过 DisplayProtocol 推送工作流事件。"""
        if self._display:
            try:
                self._display.emit(event, data)
                logger.info(f"[Orchestrator] 推送事件成功: {event}")
            except Exception as e:
                logger.warning(f"[Orchestrator] 推送事件失败: {e}")
        else:
            logger.debug(f"[Orchestrator] display 为空，跳过事件: {event}")

    def run(self, timeout: int = 1800) -> str:
        logger.info(f"[Orchestrator] 启动，{len(self.graph.nodes)} 个任务，超时 {timeout}s")
        start = time.monotonic()

        # 推送 workflow_start 事件
        tasks_info = [
            {
                "id": t.id,
                "agent": t.agent,
                "prompt": t.prompt[:100] + "..." if len(t.prompt) > 100 else t.prompt,
                "depends_on": t.depends_on,
            }
            for t in self.graph.nodes.values()
        ]
        if self._display:
            try:
                self._display.workflow_start(tasks_info, len(self.graph.nodes))
            except Exception:
                self._push_event("workflow_start", {"tasks": tasks_info, "total": len(self.graph.nodes)})

        while not self.graph.is_complete():
            if time.monotonic() - start > timeout:
                logger.warning("[Orchestrator] 超时退出")
                break

            ready = self.graph.get_ready()
            if not ready and not any(n.status == TaskStatus.RUNNING for n in self.graph.nodes.values()):
                logger.warning("[Orchestrator] 无可执行任务且无运行中任务，退出")
                break

            for task in ready:
                self.graph.mark_running(task.id)
                # 推送 task_start 事件
                task_prompt = task.prompt[:100] + "..." if len(task.prompt) > 100 else task.prompt
                if self._display:
                    try:
                        self._display.workflow_task_start(task.id, task.agent, task_prompt)
                    except Exception:
                        self._push_event("task_start", {"id": task.id, "agent": task.agent, "prompt": task_prompt})
                prompt = self.graph.resolve_prompt(task)
                # 捕获当前上下文（包含 ContextVar）
                ctx = contextvars.copy_context()
                thread = threading.Thread(
                    target=ctx.run,
                    args=(self._execute_task, task, prompt),
                    daemon=True,
                )
                thread.start()

            self._wait_pending()

            with self._lock:
                for task_id, (result, error) in list(self._pending_results.items()):
                    if result is not None:
                        self.graph.mark_done(task_id, result)
                        # 推送 task_end 事件
                        result_preview = result[:200] + "..." if result and len(result) > 200 else result
                        if self._display:
                            try:
                                self._display.workflow_task_end(task_id, TaskStatus.DONE.value, result_preview=result_preview)
                            except Exception:
                                self._push_event("task_end", {"id": task_id, "status": TaskStatus.DONE.value, "result_preview": result_preview})
                    else:
                        self.graph.mark_failed(task_id, error or "执行失败")
                        # 推送 task_end 事件
                        err_text = error or "执行失败"
                        if self._display:
                            try:
                                self._display.workflow_task_end(task_id, TaskStatus.FAILED.value, error=err_text)
                            except Exception:
                                self._push_event("task_end", {"id": task_id, "status": TaskStatus.FAILED.value, "error": err_text})
                    del self._pending_results[task_id]

        # 推送 workflow_end 事件
        elapsed = round(time.monotonic() - start, 1)
        completed = sum(1 for n in self.graph.nodes.values() if n.status == TaskStatus.DONE)
        failed = sum(1 for n in self.graph.nodes.values() if n.status == TaskStatus.FAILED)
        if self._display:
            try:
                self._display.workflow_end(elapsed, completed, failed, len(self.graph.nodes))
            except Exception:
                self._push_event("workflow_end", {"elapsed": elapsed, "completed": completed, "failed": failed, "total": len(self.graph.nodes)})

        return self._summarize()

    def _wait_pending(self):
        with self._condition:
            self._condition.wait_for(
                lambda: bool(self._pending_results) or not any(
                    n.status == TaskStatus.RUNNING for n in self.graph.nodes.values()
                ),
                timeout=30,
            )

    def _execute_task(self, task: TaskNode, prompt: str):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
        import threading

        logger.info(f"[Orchestrator] 派遣 [{task.id}] → {task.agent}: {prompt[:80]}...")

        task_timeout = task.timeout or TEAMMATE.get('task_timeout', 600)
        
        # 创建取消事件
        abort_event = threading.Event()
        
        # 注意：此方法已在 copy_context().run() 中执行，当前上下文正确
        # 需要再次捕获上下文传递给 ThreadPoolExecutor 的新线程
        ctx = contextvars.copy_context()
        
        def _run_with_abort():
            # 在 ThreadPoolExecutor 的线程中恢复上下文
            return ctx.run(self._run_task_internal, task, prompt, abort_event)

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_run_with_abort)
            try:
                result = future.result(timeout=task_timeout)
                error = None
            except FutureTimeoutError:
                logger.warning(f"[Orchestrator] 任务 [{task.id}] 超时 ({task_timeout}s)")
                abort_event.set()  # 通知任务停止
                result = None
                error = f"任务超时 ({task_timeout}s)"
            except Exception as exc:
                result = None
                error = str(exc)
                logger.error(f"[Orchestrator] 任务 [{task.id}] 异常: {exc}", exc_info=True)
        finally:
            # 不等待后台线程（超时后任务可能还在跑）
            executor.shutdown(wait=False)

        with self._condition:
            if error:
                self._pending_results[task.id] = (None, error)
            elif result:
                self._pending_results[task.id] = (result, None)
            else:
                self._pending_results[task.id] = (None, "执行返回空结果")
            self._condition.notify()

    def _run_task_internal(self, task: TaskNode, prompt: str, abort_event: threading.Event) -> str | None:
        """执行单个任务：子代理或队友，支持中断。"""
        if task.agent.startswith("subagent:"):
            return self._run_subagent(task.agent[9:], prompt, abort_event)
        else:
            return self._run_teammate(task.agent, prompt, abort_event)

    def _run_subagent(self, agent_type: str, prompt: str, abort_event: threading.Event = None) -> str | None:
        from ..tools.dispatch_subagent import execute as dispatch_exec
        result = dispatch_exec({"type": agent_type, "task": prompt}, abort_event=abort_event)
        return result

    def _run_teammate(self, agent_name: str, prompt: str, abort_event: threading.Event = None) -> str | None:
        if self.bus and self.manager:
            member = self.manager._find(agent_name)
            thread = self.manager.threads.get(agent_name)
            if member and thread and thread.is_alive():
                logger.info(f"[Orchestrator] 派发给真实队友 {agent_name}")
                task_msg = (
                    prompt
                    + '\n\n完成后用 blackboard_write 写入 key="' + agent_name + '_result"，或用 send_message 回禀 workflow。'
                )
                self.bus.send("workflow", agent_name, task_msg)
                return self._wait_teammate_result(agent_name, timeout=300)

        return self._run_oneoff_agent(agent_name, prompt)

    def _wait_teammate_result(self, agent_name: str, timeout: int = 300) -> str | None:
        start = time.monotonic()
        _MISS = object()
        result_key = f"{agent_name}_result"
        
        while time.monotonic() - start < timeout:
            # 先检查黑板和 inbox，避免错过在 wait 前已写入的结果
            result = self.blackboard.get(result_key, default=_MISS)
            if result is not _MISS and result:
                self.blackboard.put(result_key, "", author="orchestrator")
                return result
            
            inbox = self.bus.read_inbox("workflow")
            if inbox:
                for im in inbox:
                    if im.get("from") == agent_name:
                        return im.get("content", "")
            
            # 再等待变更
            remaining = timeout - (time.monotonic() - start)
            self.blackboard.wait_for_change(timeout=min(remaining, 5.0))
        
        logger.warning(f"[Orchestrator] 等待队友 {agent_name} 超时")
        if self.bus:
            self.bus.send("workflow", agent_name, "任务超时，请停止当前工作。", "shutdown_request")
        return None
        return self._run_oneoff_agent(agent_name, prompt, abort_event)

    def _run_oneoff_agent(self, agent_name: str, prompt: str, abort_event: threading.Event = None) -> str | None:
        from ..runner import run_agent
        from ..config import TEAMMATE, DATA_DIR, SKILL_PATHS as _SP
        from ..context import ContextBuilder
        from ..skills import SkillLoader
        from datetime import datetime, timezone, timedelta

        tool_names = list(TEAMMATE.get("base_tools", ["run_command", "web_fetch", "load_skill"])) + [
            "send_message", "list_teammates",
            "blackboard_read", "blackboard_write", "blackboard_list",
            "dispatch_subagent",
        ]

        team_rules = build_team_prompt(
            f"你是工作流执行者，角色 {agent_name}。",
            tool_names,
            has_messaging=False,
            completion_instruction="独立完成任务，完成后直接输出结果",  # 无需写黑板，orchestrator 会 mark_done 自动写入
            error_instruction="报告错误",
        )

        ctx_builder = ContextBuilder(DATA_DIR)
        # 加载 workspace 级技能（通过 manager 获取 workspace 技能目录）
        ws_skills = None
        if self.manager and hasattr(self.manager, 'project_dir'):
            ws_skills = self.manager.project_dir / "skills"
        _sl = SkillLoader(DATA_DIR / "skills", _SP, workspace_skills_dir=ws_skills)
        base_prompt = ctx_builder.build(skill_loader=_sl, exclude_character=True)
        system_prompt = team_rules + "\n\n---\n\n" + base_prompt if base_prompt else team_rules

        _ts = now_ts()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt, "timestamp": _ts},
        ]

        sub_display = None
        if self._display:
            try:
                sub_display = self._display.child(teammate=f"wf:{agent_name}")
            except Exception as exc:
                logger.debug(f"[Orchestrator] 创建队友 display 失败: {exc}")

        ctx = None
        if sub_display:
            from ..config import MODEL_CONFIG as _MC, RequestContext
            ctx = RequestContext(model_config=_MC, display=sub_display)

        try:
            result = run_agent(
                messages,
                max_turns=TEAMMATE.get("max_turns", 20),
                tool_names=tool_names,
                context_length=self.context_length,
                ctx=ctx,
                abort_event=abort_event,
            )
            return result
        except Exception as e:
            logger.error(f"[Orchestrator] oneoff agent {agent_name} 异常: {e}", exc_info=True)
            return f"⚠ Agent 执行失败: {type(e).__name__}: {e}"

    def _summarize(self) -> str:
        lines = [self.graph.render_status(), "", "## 结果汇总", ""]
        for node in self.graph.nodes.values():
            if node.status == TaskStatus.DONE and node.result:
                lines.append(f"### [{node.id}] ({node.agent})")
                lines.append(node.result[:2000])
                lines.append("")
            elif node.status == TaskStatus.FAILED:
                lines.append(f"### [{node.id}] ({node.agent}) — 失败")
                lines.append(node.error or "未知错误")
                lines.append("")
        return "\n".join(lines)
