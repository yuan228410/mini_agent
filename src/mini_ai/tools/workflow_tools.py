"""工作流编排工具 — run_workflow / workflow_status / load_workflow"""
import json
from pathlib import Path

import yaml

from ..logger import logger

_blackboard = None
_last_graph = None
_workflow_dirs: list[Path] = []


def configure(blackboard=None, workflow_dirs: list[Path] | None = None):
    global _blackboard, _workflow_dirs
    if blackboard is not None:
        _blackboard = blackboard
    if workflow_dirs is not None:
        _workflow_dirs = workflow_dirs


# ── run_workflow ──

_run_def = {
    "type": "function",
    "function": {
        "name": "run_workflow",
        "description": (
            "提交并执行一个多 agent 工作流（DAG）。定义任务节点和依赖关系，"
            "系统自动按依赖顺序编排执行：无依赖的任务并行，有依赖的等前置完成后触发。\n"
            "每个任务的 prompt 中可用 {task_id} 引用依赖任务的结果。\n"
            "示例：[{\"id\":\"search\",\"agent\":\"researcher\",\"prompt\":\"搜索 X\",\"depends_on\":[]},"
            "{\"id\":\"code\",\"agent\":\"coder\",\"prompt\":\"根据搜索结果编码: {search}\",\"depends_on\":[\"search\"]}]"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "任务节点列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "任务唯一 ID"},
                            "agent": {"type": "string", "description": "执行者名称（teammate 名或 subagent:type）"},
                            "prompt": {"type": "string", "description": "任务描述，{dep_id} 引用依赖结果"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "依赖的任务 ID 列表",
                            },
                        },
                        "required": ["id", "agent", "prompt"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
}


def _run_exec(args: dict) -> str:
    global _last_graph

    from ..team.task_graph import TaskGraph, TaskNode
    from ..team.orchestrator import Orchestrator
    from ..config import MODEL_CONFIG

    tasks = args.get("tasks", [])
    if not tasks:
        return "Error: tasks 列表为空"

    graph = TaskGraph(_blackboard)
    for t in tasks:
        node = TaskNode(
            id=t["id"],
            agent=t["agent"],
            prompt=t["prompt"],
            depends_on=t.get("depends_on", []),
            condition=t.get("condition"),
            max_retry=t.get("max_retry", 1),
        )
        graph.add_task(node)

    _last_graph = graph
    logger.info(f"[Workflow] 启动工作流，{len(tasks)} 个任务")

    orch = Orchestrator(
        graph, _blackboard,
        context_length=MODEL_CONFIG.get("context_length", 128000),
    )
    result = orch.run()
    return result


# ── workflow_status ──

_status_def = {
    "type": "function",
    "function": {
        "name": "workflow_status",
        "description": "查看最近一次工作流的执行状态。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _status_exec(args: dict) -> str:
    if _last_graph is None:
        return "暂无工作流记录"
    return _last_graph.render_status()


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition, execute):
        self.definition = definition
        self.execute = execute


run_workflow_mod = _ToolMod(_run_def, _run_exec)
workflow_status_mod = _ToolMod(_status_def, _status_exec)

# ── load_workflow ──

_load_def = {
    "type": "function",
    "function": {
        "name": "load_workflow",
        "description": "加载预定义的工作流模板。使用 name='list' 列出所有可用模板。加载后返回 tasks JSON 可直接传给 run_workflow。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工作流模板名称，或 'list' 列出全部"},
            },
            "required": ["name"],
        },
    },
}


def _load_exec(args: dict) -> str:
    name = args.get("name", "").strip()

    if name == "list":
        templates = []
        for d in _workflow_dirs:
            if d.exists():
                for f in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")):
                    templates.append(f.stem)
        if not templates:
            return "暂无预定义工作流模板"
        return "可用模板:\n" + "\n".join(f"  - {t}" for t in templates)

    for d in _workflow_dirs:
        for ext in (".yaml", ".yml"):
            path = d / f"{name}{ext}"
            if path.exists():
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
                    tasks = data.get("tasks", [])
                    return json.dumps(tasks, ensure_ascii=False, indent=2)
                except Exception as e:
                    return f"Error: 解析 {path} 失败: {e}"

    return f"Error: 工作流模板 '{name}' 不存在"


load_workflow_mod = _ToolMod(_load_def, _load_exec)

ALL_WORKFLOW_TOOLS = [run_workflow_mod, workflow_status_mod, load_workflow_mod]
