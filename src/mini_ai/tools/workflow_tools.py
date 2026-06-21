"""工作流编排工具 — run_workflow / workflow_status / load_workflow"""
import json
import threading
from collections.abc import Mapping
from pathlib import Path

import yaml

from ..core.display_protocol import DisplayProtocol
from ..core.runtime_types import BlackboardProtocol, MessageBusProtocol, TeamManagerProtocol, ToolArgs, ToolDefinition, WorkflowTaskInput
from ..logger import logger

WorkflowGraphStore = dict[int, object]


def _arg_text(args: ToolArgs | Mapping[str, object], key: str, default: str = "") -> str:
    value = args.get(key, default)
    return value if isinstance(value, str) else str(value)


def _arg_int(args: ToolArgs | Mapping[str, object], key: str, default: int = 0) -> int:
    value = args.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _depends_on(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item if isinstance(item, str) else str(item) for item in value]


def normalize_workflow_tasks(raw_tasks: object) -> list[WorkflowTaskInput]:
    if not isinstance(raw_tasks, list):
        return []
    tasks: list[WorkflowTaskInput] = []
    for raw in raw_tasks:
        if not isinstance(raw, Mapping):
            continue
        task_id = _arg_text(raw, "id").strip()
        agent = _arg_text(raw, "agent").strip()
        prompt = _arg_text(raw, "prompt")
        if not task_id or not agent or not prompt:
            continue
        task: WorkflowTaskInput = {
            "id": task_id,
            "agent": agent,
            "prompt": prompt,
            "depends_on": _depends_on(raw.get("depends_on", [])),
            "max_retry": _arg_int(raw, "max_retry", 1),
            "timeout": _arg_int(raw, "timeout", 0),
        }
        condition = raw.get("condition")
        if condition:
            task["condition"] = condition if isinstance(condition, str) else str(condition)
        tasks.append(task)
    return tasks


# ── run_workflow ──

_run_def: ToolDefinition = {
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


def run_workflow_with_context(
    args: ToolArgs,
    *,
    blackboard: BlackboardProtocol,
    graphs: WorkflowGraphStore,
    bus: MessageBusProtocol | None = None,
    manager: TeamManagerProtocol | None = None,
    display: DisplayProtocol | None = None,
    derived_agent_resources=None,
) -> str:
    from ..team.task_graph import TaskGraph, TaskNode
    from ..team.orchestrator import Orchestrator
    from ..config import MODEL_CONFIG

    tasks = normalize_workflow_tasks(args.get("tasks", []))
    if not tasks:
        return "Error: tasks 列表为空"

    graph = TaskGraph(blackboard)
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

    graphs[threading.current_thread().ident] = graph
    logger.info(f"[Workflow] 启动工作流，{len(tasks)} 个任务")

    settings = getattr(derived_agent_resources, "settings", None)
    context_length = settings.model.context_length if settings else MODEL_CONFIG.get("context_length", 256000)
    orch = Orchestrator(
        graph, blackboard,
        context_length=context_length,
        bus=bus, manager=manager, display=display,
        derived_agent_resources=derived_agent_resources,
    )
    result = orch.run()
    return result


def _run_exec(args: ToolArgs) -> str:
    return "Error: workflow tools are not configured with a blackboard"


# ── workflow_status ──

_status_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "workflow_status",
        "description": "查看最近工作流执行状态。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def workflow_status_from_graphs(graphs: WorkflowGraphStore, graphs_lock: threading.Lock) -> str:
    graph = graphs.get(threading.current_thread().ident)
    if graph is None:
        with graphs_lock:
            if graphs:
                graph = next(iter(graphs.values()))
    if graph is None:
        return "暂无工作流记录"
    return graph.render_status()


def _status_exec(args: ToolArgs) -> str:
    return "暂无工作流记录"


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition: ToolDefinition, execute):
        self.definition = definition
        self.execute = execute


run_workflow_mod = _ToolMod(_run_def, _run_exec)
workflow_status_mod = _ToolMod(_status_def, _status_exec)

# ── load_workflow ──

_load_def: ToolDefinition = {
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


def load_workflow_from_dirs(args: ToolArgs, workflow_dirs: list[Path]) -> str:
    name = _arg_text(args, "name").strip()

    if name == "list":
        templates = []
        for d in workflow_dirs:
            if d.exists():
                for f in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")):
                    templates.append(f.stem)
        if not templates:
            return "暂无预定义工作流模板"
        return "可用模板:\n" + "\n".join(f"  - {t}" for t in templates)

    for d in workflow_dirs:
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


def _load_exec(args: ToolArgs) -> str:
    return load_workflow_from_dirs(args, [])


load_workflow_mod = _ToolMod(_load_def, _load_exec)

ALL_WORKFLOW_TOOLS = [run_workflow_mod, workflow_status_mod, load_workflow_mod]
