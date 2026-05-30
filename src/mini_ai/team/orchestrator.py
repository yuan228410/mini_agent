"""DAG 驱动的多 agent 编排器"""
import threading
import time

from .blackboard import Blackboard
from ..config import TEAMMATE
from ..logger import logger
from .prompts import build_team_prompt
from .task_graph import TaskGraph, TaskNode


class Orchestrator:

    def __init__(self, graph: TaskGraph, blackboard: Blackboard, *, context_length: int = 128000, bus=None, manager=None):
        self.graph = graph
        self.blackboard = blackboard
        self.context_length = context_length
        self.bus = bus
        self.manager = manager
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._pending_results: dict[str, tuple[str | None, str | None]] = {}

    def run(self, timeout: int = 1800) -> str:
        logger.info(f"[Orchestrator] 启动，{len(self.graph.nodes)} 个任务，超时 {timeout}s")
        start = time.monotonic()

        while not self.graph.is_complete():
            if time.monotonic() - start > timeout:
                logger.warning("[Orchestrator] 超时退出")
                break

            ready = self.graph.get_ready()
            if not ready and not any(n.status == "running" for n in self.graph.nodes.values()):
                logger.warning("[Orchestrator] 无可执行任务且无运行中任务，退出")
                break

            for task in ready:
                self.graph.mark_running(task.id)
                prompt = self.graph.resolve_prompt(task)
                thread = threading.Thread(
                    target=self._execute_task,
                    args=(task, prompt),
                    daemon=True,
                )
                thread.start()

            self._wait_pending()

            with self._lock:
                for task_id, (result, error) in list(self._pending_results.items()):
                    if result is not None:
                        self.graph.mark_done(task_id, result)
                    else:
                        self.graph.mark_failed(task_id, error or "执行失败")
                    del self._pending_results[task_id]

        return self._summarize()

    def _wait_pending(self):
        with self._condition:
            self._condition.wait_for(
                lambda: bool(self._pending_results) or not any(
                    n.status == "running" for n in self.graph.nodes.values()
                ),
                timeout=30,
            )

    def _execute_task(self, task: TaskNode, prompt: str):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

        logger.info(f"[Orchestrator] 派遣 [{task.id}] → {task.agent}: {prompt[:80]}...")

        task_timeout = task.timeout or TEAMMATE.get('task_timeout', 600)

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(self._run_task, task, prompt)
            try:
                result = future.result(timeout=task_timeout)
                error = None
            except FutureTimeoutError:
                logger.warning(f"[Orchestrator] 任务 [{task.id}] 超时 ({task_timeout}s)")
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

    def _run_task(self, task: TaskNode, prompt: str) -> str | None:
        """执行单个任务：子代理或队友。"""
        if task.agent.startswith("subagent:"):
            return self._run_subagent(task.agent[9:], prompt)
        else:
            return self._run_teammate(task.agent, prompt)

    def _run_subagent(self, agent_type: str, prompt: str) -> str | None:
        from ..tools.dispatch_subagent import execute as dispatch_exec
        result = dispatch_exec({"type": agent_type, "task": prompt})
        return result

    def _run_teammate(self, agent_name: str, prompt: str) -> str | None:
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
        while time.monotonic() - start < timeout:
            remaining = timeout - (time.monotonic() - start)
            self.blackboard.wait_for_change(timeout=min(remaining, 5.0))
            result = self.blackboard.get(f"{agent_name}_result", default=_MISS)
            if result is not _MISS and result:
                self.blackboard.put(f"{agent_name}_result", "", author="orchestrator")
                return result
            inbox = self.bus.read_inbox("workflow")
            if inbox:
                for im in inbox:
                    if im.get("from") == agent_name:
                        return im.get("content", "")
        logger.warning(f"[Orchestrator] 等待队友 {agent_name} 超时")
        if self.bus:
            self.bus.send("workflow", agent_name, "任务超时，请停止当前工作。", "shutdown_request")
        return None

    def _run_oneoff_agent(self, agent_name: str, prompt: str) -> str | None:
        from ..runner import run_agent
        from ..config import TEAMMATE, DATA_DIR, SKILL_PATHS as _SP
        from ..context import ContextBuilder
        from ..skills import SkillLoader

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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        sub_display = None
        if self.bus and hasattr(self.bus, '_on_send'):
            try:
                from ..tools import _registry
                lead_display = _registry._display
                if lead_display and hasattr(lead_display, 'queue'):
                    from ..web.display import WebDisplay
                    sub_display = WebDisplay(lead_display.queue, lead_display.loop)
                    sub_display.set_teammate(f"wf:{agent_name}")
            except (ImportError, AttributeError) as exc:
                logger.debug(f"[Orchestrator] 创建队友 display 失败: {exc}")

        ctx = None
        if sub_display:
            from ..config import MODEL_CONFIG as _MC, RequestContext
            ctx = RequestContext(model_config=_MC, display=sub_display)

        result = run_agent(
            messages,
            max_turns=TEAMMATE.get("max_turns", 20),
            tool_names=tool_names,
            context_length=self.context_length,
            ctx=ctx,
        )
        return result

    def _summarize(self) -> str:
        lines = [self.graph.render_status(), "", "## 结果汇总", ""]
        for node in self.graph.nodes.values():
            if node.status == "done" and node.result:
                lines.append(f"### [{node.id}] ({node.agent})")
                lines.append(node.result[:2000])
                lines.append("")
            elif node.status == "failed":
                lines.append(f"### [{node.id}] ({node.agent}) — 失败")
                lines.append(node.error or "未知错误")
                lines.append("")
        return "\n".join(lines)
