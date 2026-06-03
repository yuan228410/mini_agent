"""工作流编排工具 — run_workflow / workflow_status / load_workflow"""
import json
import threading
from pathlib import Path

import yaml

from ..logger import logger

_blackboard = None
_workflow_dirs: list[Path] = []
_graphs_lock = threading.Lock()
_last_graphs: dict[int, object] = {}  # thread_id → last TaskGraph


_bus = None
_manager = None

def configure(blackboard=None, workflow_dirs: list[Path] | None = None, bus=None, manager=None):
    global _blackboard, _workflow_dirs, _bus, _manager
    if blackboard is not None:
        _blackboard = blackboard
    if workflow_dirs is not None:
        _workflow_dirs = workflow_dirs
    if bus is not None:
        _bus = bus
    if manager is not None:
        _manager = manager


# ── run_workflow ──

_run_def = {
    "type": "function",
    "function": {
        "name": "run_workflow",
        "description": "执行多 agent 工作流（DAG）。无依赖任务并行，有依赖任务串行。prompt 中用 {task_id} 引用依赖结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "任务节点列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "任务 ID"},
                            "agent": {"type": "string", "description": "执行者（teammate 名或 subagent:type）"},
                            "prompt": {"type": "string", "description": "任务描述，{dep_id} 引用依赖结果"},
                            "depends_on": {"type": "array", "items": {"type": "string"}, "description": "依赖的任务 ID"},
                            "timeout": {"type": "integer", "description": "超时秒数，默认 600"},
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
            timeout=t.get("timeout", 0),
        )
        graph.add_task(node)

    _last_graphs[threading.current_thread().ident] = graph
    logger.info(f"[Workflow] 启动工作流，{len(tasks)} 个任务")

    # 获取 display 用于推送事件
    display = None
    try:
        from ..tools import _registry
        display = _registry._display
    except (ImportError, AttributeError):
        pass

    orch = Orchestrator(
        graph, _blackboard,
        context_length=MODEL_CONFIG.get("context_length", 256000),
        bus=_bus, manager=_manager, display=display,
    )
    result = orch.run()
    return result


# ── workflow_status ──

_status_def = {
    "type": "function",
    "function": {
        "name": "workflow_status",
        "description": "查看最近工作流执行状态。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _status_exec(args: dict) -> str:
    graph = _last_graphs.get(threading.current_thread().ident)
    if graph is None:
        with _graphs_lock:
            if _last_graphs:
                graph = next(iter(_last_graphs.values()))
    if graph is None:
        return "暂无工作流记录"
    return graph.render_status()


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
