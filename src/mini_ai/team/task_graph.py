"""轻量 DAG 调度器 — 任务依赖图 + 状态追踪"""
from __future__ import annotations
import threading
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
    timeout: int = 0  # 单任务超时秒数，0 表示使用默认 600s
    fail_on_dep_failure: bool = True  # 依赖失败时是否标记为失败（True=失败，False=仍可执行）


class TaskGraph:

    def __init__(self, blackboard: Blackboard):
        self.nodes: dict[str, TaskNode] = {}
        self.blackboard = blackboard
        self._lock = threading.Lock()  # 添加线程锁

    def add_task(self, node: TaskNode):
        with self._lock:
            self.nodes[node.id] = node
            logger.info(f"[DAG] 注册任务 {node.id} agent={node.agent} depends={node.depends_on}")

    def get_ready(self) -> list[TaskNode]:
        with self._lock:
            ready = []
            for node in self.nodes.values():
                if node.status != "pending":
                    continue
                
                # 检查依赖状态
                deps_done = all(
                    self.nodes[dep].status == "done"
                    for dep in node.depends_on
                    if dep in self.nodes
                )
                deps_failed = any(
                    self.nodes[dep].status == "failed"
                    for dep in node.depends_on
                    if dep in self.nodes
                )
                
                # 依赖有失败的情况
                if deps_failed:
                    if node.fail_on_dep_failure:
                        # 标记为失败（传播失败）
                        node.status = "failed"
                        failed_deps = [dep for dep in node.depends_on if dep in self.nodes and self.nodes[dep].status == "failed"]
                        node.error = f"依赖任务失败: {', '.join(failed_deps)}"
                        logger.warning(f"[DAG] {node.id} 因依赖失败而标记失败: {node.error}")
                        continue
                    # 否则继续检查是否可以执行
                
                # 依赖未全部完成
                if not deps_done and not deps_failed:
                    continue
                
                # 检查条件
                if node.condition and not self._evaluate_condition_locked(node):
                    node.status = "skipped"
                    logger.info(f"[DAG] {node.id} 条件不满足，跳过")
                    continue
                
                ready.append(node)
            return ready

    @staticmethod
    def _safe_eval(expr: str, ctx: dict) -> bool:
        """安全条件求值：仅支持比较、逻辑运算和属性访问，禁止任意代码执行。"""
        import ast
        import operator as op

        _OPS = {
            ast.Eq: op.eq, ast.NotEq: op.ne,
            ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
            ast.And: lambda a, b: a and b, ast.Or: lambda a, b: a or b,
        }
        _UNARY_OPS = {ast.Not: op.not_}

        def _resolve(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                return ctx.get(node.id, "")
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("_"):
                    raise ValueError(f"禁止访问私有属性: {node.attr}")
                val = _resolve(node.value)
                if isinstance(val, dict):
                    return val.get(node.attr, "")  # TODO: 若开放外部 DAG 模板，需加白名单限制属性访问
                if isinstance(val, str):
                    return getattr(val, node.attr, "")
                raise ValueError(f"不支持对 {type(val).__name__} 的属性访问")
            if isinstance(node, ast.Subscript):
                val = _resolve(node.value)
                key = _resolve(node.slice)
                if isinstance(val, dict):
                    return val.get(key, "")
                return getattr(val, key, "")
            if isinstance(node, ast.BoolOp):
                result = _resolve(node.values[0])
                for v in node.values[1:]:
                    if isinstance(node.op, ast.And):
                        result = result and _resolve(v)
                    else:
                        result = result or _resolve(v)
                return result
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return not _resolve(node.operand)
            if isinstance(node, ast.Compare):
                left = _resolve(node.left)
                for op_node, comp_node in zip(node.ops, node.comparators):
                    right = _resolve(comp_node)
                    op_fn = _OPS.get(type(op_node))
                    if op_fn is None:
                        raise ValueError(f"不支持的操作符: {type(op_node).__name__}")
                    if not op_fn(left, right):
                        return False
                    left = right
                return True
            raise ValueError(f"不支持的表达式: {type(node).__name__}")

        tree = ast.parse(expr, mode="eval")
        return bool(_resolve(tree.body))

    def _evaluate_condition_locked(self, node: TaskNode) -> bool:
        """在锁内评估条件（内部方法）"""
        ctx = {}
        for dep_id, dep_node in self.nodes.items():
            ctx[dep_id] = {
                "status": dep_node.status,
                "result": dep_node.result or "",
                "error": dep_node.error or "",
            }
        ctx["blackboard"] = self.blackboard.snapshot()
        try:
            return self._safe_eval(node.condition, ctx)
        except Exception as e:
            logger.warning(f"[DAG] 条件表达式求值失败 ({node.condition}): {e}")
            return True

    def _evaluate_condition(self, node: TaskNode) -> bool:
        """评估条件（公开方法，带锁）"""
        with self._lock:
            return self._evaluate_condition_locked(node)

    def mark_running(self, task_id: str):
        with self._lock:
            node = self.nodes.get(task_id)
            if node:
                node.status = "running"
                logger.info(f"[DAG] {task_id} → running")

    def mark_done(self, task_id: str, result: str):
        with self._lock:
            node = self.nodes.get(task_id)
            if not node:
                return
            node.status = "done"
            node.result = result
        # blackboard.put 在锁外执行，避免嵌套锁
        node = self.nodes.get(task_id)
        if node:
            self.blackboard.put(task_id, result, author=node.agent)
            logger.info(f"[DAG] {task_id} → done (result {len(result)} chars)")

    def mark_failed(self, task_id: str, error: str):
        with self._lock:
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
