"""工具注册与分发"""
import json
from concurrent.futures import ThreadPoolExecutor

from tools import dispatch_subagent, run_command, update_todos, web_fetch

_PARALLEL_TOOLS = {"dispatch_subagent"}

_ALL_TOOLS = [run_command, web_fetch, update_todos]
_BY_NAME: dict[str, object] = {}


def _rebuild_index():
    _BY_NAME.clear()
    _BY_NAME.update({m.definition["function"]["name"]: m for m in _ALL_TOOLS})


_rebuild_index()


def register(skill_loader) -> None:
    """注入 skill_loader 依赖，注册技能相关工具"""
    from tools import list_skills, load_skill

    list_skills._loader = skill_loader
    load_skill._loader = skill_loader
    _ALL_TOOLS.extend([list_skills, load_skill])
    _rebuild_index()


def register_subagents(subagent_loader) -> None:
    """注册子代理调度工具"""
    subagent_list = subagent_loader.list_specs()
    dispatch_subagent.definition = dispatch_subagent.build_definition(subagent_list)
    dispatch_subagent._loader = subagent_loader

    _ALL_TOOLS.append(dispatch_subagent)
    _rebuild_index()


def get_definitions() -> list[dict]:
    """返回所有工具的 OpenAI 定义列表"""
    return [m.definition for m in _ALL_TOOLS]


def dispatch(name: str, args: dict) -> str | None:
    """根据工具名分发执行，返回结果或 None"""
    mod = _BY_NAME.get(name)
    return mod.execute(args) if mod else None


def handle_tool_calls(msg: dict, messages: list[dict]) -> None:
    """处理消息中的 tool_calls，并行安全工具并发执行，其余串行"""
    calls = msg["tool_calls"]
    messages.append({"role": "assistant", "content": None, "tool_calls": calls})

    i = 0
    while i < len(calls):
        tc = calls[i]
        name = tc["function"]["name"]

        if name in _PARALLEL_TOOLS:
            # 收集连续的可并行调用
            group = []
            while i < len(calls) and calls[i]["function"]["name"] in _PARALLEL_TOOLS:
                group.append(calls[i])
                i += 1
            _execute_parallel(group, messages)
        else:
            _execute_one(tc, messages)
            i += 1


def _execute_one(tc: dict, messages: list[dict]) -> None:
    args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
    output = dispatch(tc["function"]["name"], args)
    if output is not None:
        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})


def _execute_parallel(calls: list[dict], messages: list[dict]) -> None:
    results = {}

    def _run(tc):
        args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
        return tc["id"], dispatch(tc["function"]["name"], args)

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        for tc_id, output in pool.map(_run, calls):
            if output is not None:
                results[tc_id] = output

    for tc in calls:
        if tc["id"] in results:
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": results[tc["id"]]})


def render_todos() -> str:
    """返回当前待办列表的文本表示"""
    return update_todos._store.render()