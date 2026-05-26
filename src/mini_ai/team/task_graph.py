"""轻量 DAG 调度器 — 任务依赖图 + 状态追踪"""
from __future__ import annotations

from dataclasses import dataclass, field

from .blackboard import Blackboard
from ..logger import logger


@dataclass
class TaskNode:
    id: str
    agent: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    condition: str | None = None  # e.g. "search.status == 'done'" or "error in {search}"
    status: str = "pending"
    result: str | None = None
    error: str | None = None
    retry_count: int = 0
    max_retry: int = 1


class TaskGraph:

    def __init__(self, blackboard: Blackboard):
        self.nodes: dict[str, TaskNode] = {}
        self.blackboard = blackboard

    def add_task(self, node: TaskNode):
        self.nodes[node.id] = node
        logger.info(f"[DAG] 注册任务 {node.id} agent={node.agent} depends={node.depends_on}")

    def get_ready(self) -> list[TaskNode]:
        ready = []
        for node in self.nodes.values():
            if node.status != "pending":
                continue
            deps_met = all(
                self.nodes[dep].status in ("done", "failed")
                for dep in node.depends_on
                if dep in self.nodes
            )
            if not deps_met:
                continue
            if node.condition and not self._evaluate_condition(node):
                node.status = "skipped"
                logger.info(f"[DAG] {node.id} 条件不满足，跳过")
                continue
            ready.append(node)
        return ready

    def _evaluate_condition(self, node: TaskNode) -> bool:
        ctx = {}
        for dep_id, dep_node in self.nodes.items():
            ctx[dep_id] = {
                "status": dep_node.status,
                "result": dep_node.result or "",
                "error": dep_node.error or "",
            }
        ctx["blackboard"] = self.blackboard.snapshot()
        try:
            return bool(eval(node.condition, {"__builtins__": {}}, ctx))
        except Exception as e:
            logger.warning(f"[DAG] 条件表达式求值失败 ({node.condition}): {e}")
            return True

    def mark_running(self, task_id: str):
        node = self.nodes.get(task_id)
        if node:
            node.status = "running"
            logger.info(f"[DAG] {task_id} → running")

    def mark_done(self, task_id: str, result: str):
        node = self.nodes.get(task_id)
        if not node:
            return
        node.status = "done"
        node.result = result
        self.blackboard.put(task_id, result, author=node.agent)
        logger.info(f"[DAG] {task_id} → done (result {len(result)} chars)")

    def mark_failed(self, task_id: str, error: str):
        node = self.nodes.get(task_id)
        if not node:
            return
        node.retry_count += 1
        if node.retry_count < node.max_retry:
            node.status = "pending"
            node.error = error
            logger.warning(f"[DAG] {task_id} 失败，将重试 ({node.retry_count}/{node.max_retry}): {error[:100]}")
        else:
            node.status = "failed"
            node.error = error
            logger.error(f"[DAG] {task_id} → failed: {error[:100]}")

    def is_complete(self) -> bool:
        return all(n.status in ("done", "failed", "skipped") for n in self.nodes.values())

    def has_runnable(self) -> bool:
        return bool(self.get_ready()) or any(n.status == "running" for n in self.nodes.values())

    def resolve_prompt(self, node: TaskNode) -> str:
        prompt = node.prompt
        for dep_id in node.depends_on:
            dep_result = self.blackboard.get(dep_id, f"[{dep_id} 结果未找到]")
            prompt = prompt.replace(f"{{{dep_id}}}", dep_result)
        all_keys = self.blackboard.snapshot()
        for key, val in all_keys.items():
            prompt = prompt.replace(f"{{blackboard.{key}}}", val)
        return prompt

    def render_status(self) -> str:
        if not self.nodes:
            return "无工作流任务"
        lines = ["工作流状态:"]
        status_icons = {"pending": "⏳", "running": "▶️", "done": "✅", "failed": "❌", "skipped": "⏭️"}
        for node in self.nodes.values():
            icon = status_icons.get(node.status, "?")
            deps = f" (依赖: {', '.join(node.depends_on)})" if node.depends_on else ""
            lines.append(f"  {icon} [{node.id}] {node.agent}{deps} — {node.status}")
        done_count = sum(1 for n in self.nodes.values() if n.status == "done")
        total = len(self.nodes)
        lines.append(f"  进度: {done_count}/{total}")
        return "\n".join(lines)
