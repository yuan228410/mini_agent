"""工具注册与分发"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import TOOL
from ..logger import logger
from . import dispatch_subagent, read_file, run_command, update_todos, web_fetch, write_file

_MAX_RESULT_CHARS = TOOL["max_result_chars"]
_display = None


def _truncate(output: str) -> str:
    if len(output) <= _MAX_RESULT_CHARS:
        return output
    return output[:_MAX_RESULT_CHARS] + f"\n[已截断，原长 {len(output)} 字符]"

_PARALLEL_TOOLS = {"dispatch_subagent", "spawn_teammate"}

_ALL_TOOLS = [read_file, write_file, run_command, web_fetch, update_todos]
_BY_NAME: dict[str, object] = {}


def _rebuild_index():
    _BY_NAME.clear()
    _BY_NAME.update({m.definition["function"]["name"]: m for m in _ALL_TOOLS})


_rebuild_index()


def register(skill_loader) -> None:
    """注入 skill_loader 依赖，注册技能相关工具"""
    from . import list_skills, load_skill

    list_skills.configure(loader=skill_loader)
    load_skill.configure(loader=skill_loader)
    _ALL_TOOLS.extend([list_skills, load_skill])
    _rebuild_index()


def register_subagents(subagent_loader) -> None:
    """注册子代理调度工具"""
    subagent_list = subagent_loader.list_specs()
    dispatch_subagent.configure(
        loader=subagent_loader,
        definition=dispatch_subagent.build_definition(subagent_list),
    )

    _ALL_TOOLS.append(dispatch_subagent)
    _rebuild_index()


def register_team(bus, manager) -> None:
    """注册 team 协作工具"""
    from . import team_tools
    team_tools.configure(bus=bus, manager=manager)
    team_tools.set_caller("lead")
    _ALL_TOOLS.extend(team_tools.ALL_TEAM_TOOLS)
    _rebuild_index()

def register_display(display) -> None:
    """注入 display 实例"""
    global _display
    _display = display


def get_definitions() -> list[dict]:
    """返回所有工具的 OpenAI 定义列表"""
    return [m.definition for m in _ALL_TOOLS]


def dispatch(name: str, args: dict) -> str | None:
    """根据工具名分发执行，返回结果或 None"""
    mod = _BY_NAME.get(name)
    return mod.execute(args) if mod else None


def handle_tool_calls(msg: dict, messages: list[dict]) -> bool:
    """处理消息中的 tool_calls，返回是否包含 spawn_teammate"""
    calls = msg["tool_calls"]
    messages.append({"role": "assistant", "content": None, "tool_calls": calls})

    spawned = False
    i = 0
    while i < len(calls):
        tc = calls[i]
        name = tc["function"]["name"]

        if name == "spawn_teammate":
            spawned = True

        if name in _PARALLEL_TOOLS:
            group = []
            while i < len(calls) and calls[i]["function"]["name"] in _PARALLEL_TOOLS:
                group.append(calls[i])
                i += 1
            _execute_parallel(group, messages)
        else:
            _execute_one(tc, messages)
            i += 1

    return spawned


def _execute_one(tc: dict, messages: list[dict]) -> None:
    name = tc["function"]["name"]
    args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
    logger.info(f"[工具→] {name}({json.dumps(args, ensure_ascii=False)})")
    args_summary = json.dumps(args, ensure_ascii=False)
    if _display:
        _display.tool_call_start(name, args_summary)
    t0 = time.monotonic()
    output = dispatch(name, args)
    elapsed = time.monotonic() - t0
    if output is not None:
        output = _truncate(output)
        logger.debug(f"[工具←] {name} len={len(output)}")
        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})
    if _display:
        _display.tool_result(name, output or "", elapsed)


def _execute_parallel(calls: list[dict], messages: list[dict]) -> None:
    import contextvars as _cv
    results = {}
    caller_val = _cv.copy_context()

    def _run(tc):
        try:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
            logger.info(f"[并行→] {name}({json.dumps(args, ensure_ascii=False)})")
            if _display:
                _display.tool_call_start(name, json.dumps(args, ensure_ascii=False))
            t0 = time.monotonic()
            result = caller_val.run(dispatch, name, args)
            elapsed = time.monotonic() - t0
            if _display and result is not None:
                _display.tool_result(name, result, elapsed)
            return tc["id"], result
        except Exception as e:
            return tc["id"], f"执行失败: {e}"

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = {pool.submit(_run, tc): tc for tc in calls}
        for future in as_completed(futures):
            tc_id, output = future.result()
            if output is not None:
                results[tc_id] = output

    for tc in calls:
        if tc["id"] in results:
            output = _truncate(results[tc["id"]])
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})


def render_todos() -> str:
    """返回当前待办列表的文本表示"""
    return update_todos._store.render()
