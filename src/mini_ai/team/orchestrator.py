"""DAG 驱动的多 agent 编排器"""
import threading
import time

from .blackboard import Blackboard
from ..logger import logger
from .task_graph import TaskGraph, TaskNode


class Orchestrator:

    def __init__(self, graph: TaskGraph, blackboard: Blackboard, *, context_length: int = 128000):
        self.graph = graph
        self.blackboard = blackboard
        self.context_length = context_length
        self._completion_event = threading.Event()
        self._results_lock = threading.Lock()
        self._pending_results: dict[str, str | None] = {}

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

            self._completion_event.clear()
            self._completion_event.wait(timeout=5)

            with self._results_lock:
                for task_id, result in list(self._pending_results.items()):
                    if result is not None:
                        self.graph.mark_done(task_id, result)
                    else:
                        node = self.graph.nodes[task_id]
                        self.graph.mark_failed(task_id, node.error or "执行失败")
                    del self._pending_results[task_id]

        return self._summarize()

    def _execute_task(self, task: TaskNode, prompt: str):
        from ..runner import run_agent

        logger.info(f"[Orchestrator] 派遣 [{task.id}] → {task.agent}: {prompt[:80]}...")

        if task.agent.startswith("subagent:"):
            result = self._run_subagent(task.agent[9:], prompt)
        else:
            result = self._run_teammate(task.agent, prompt)

        with self._results_lock:
            if result:
                self._pending_results[task.id] = result
            else:
                task.error = "执行返回空结果"
                self._pending_results[task.id] = None

        self._completion_event.set()

    def _run_subagent(self, agent_type: str, prompt: str) -> str | None:
        from ..tools.dispatch_subagent import execute as dispatch_exec
        result = dispatch_exec({"type": agent_type, "task": prompt})
        return result

    def _run_teammate(self, agent_name: str, prompt: str) -> str | None:
        from ..runner import run_agent
        from ..config import MODEL_CONFIG

        system_prompt = (
            f"你是工作流中的执行者，角色是 {agent_name}。\n"
            f"请完成以下任务并返回结果。你可以使用 run_command、web_fetch、load_skill、"
            "blackboard_read、blackboard_write 工具。\n"
        )

        from ..config import TEAMMATE
        tool_names = list(TEAMMATE.get("base_tools", ["run_command", "web_fetch", "load_skill"])) + [
            "blackboard_read", "blackboard_write", "blackboard_list",
        ]

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        result = run_agent(
            messages,
            max_turns=TEAMMATE.get("max_turns", 20),
            tool_names=tool_names,
            context_length=self.context_length,
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
