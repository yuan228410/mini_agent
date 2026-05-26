"""工具注册与分发"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import TOOL
from ..logger import logger
from . import dispatch_subagent, read_file, run_command, update_todos, web_fetch, write_file

_MAX_RESULT_CHARS = TOOL["max_result_chars"]


def _truncate(output: str) -> str:
    if len(output) <= _MAX_RESULT_CHARS:
        return output
    return output[:_MAX_RESULT_CHARS] + f"\n[已截断，原长 {len(output)} 字符]"


class ToolRegistry:

    def __init__(self):
        self._tools: list = []
        self._by_name: dict[str, object] = {}
        self._display = None
        self._parallel_tools: set[str] = {"dispatch_subagent", "spawn_teammate"}

    def _rebuild_index(self):
        self._by_name.clear()
        self._by_name.update({m.definition["function"]["name"]: m for m in self._tools})

    def add_tools(self, *modules):
        self._tools.extend(modules)
        self._rebuild_index()

    def register_skills(self, skill_loader):
        from . import list_skills, load_skill, install_skill
        list_skills.configure(loader=skill_loader)
        load_skill.configure(loader=skill_loader)
        install_skill.configure(loader=skill_loader)
        self.add_tools(list_skills, load_skill, install_skill)

    def register_subagents(self, subagent_loader):
        subagent_list = subagent_loader.list_specs()
        dispatch_subagent.configure(
            loader=subagent_loader,
            definition=dispatch_subagent.build_definition(subagent_list),
        )
        self.add_tools(dispatch_subagent)

    def register_team(self, bus, manager):
        from . import team_tools
        team_tools.configure(bus=bus, manager=manager)
        team_tools.set_caller("lead")
        self.add_tools(*team_tools.ALL_TEAM_TOOLS)

    def register_display(self, display):
        self._display = display

    def get_definitions(self) -> list[dict]:
        return [m.definition for m in self._tools]

    def dispatch(self, name: str, args: dict) -> str | None:
        mod = self._by_name.get(name)
        return mod.execute(args) if mod else None

    def handle_tool_calls(self, msg: dict, messages: list[dict], display=None) -> bool:
        calls = msg["tool_calls"]
        messages.append({"role": "assistant", "content": None, "tool_calls": calls})

        _disp = display if display is not None else self._display
        spawned = False
        i = 0
        while i < len(calls):
            tc = calls[i]
            name = tc["function"]["name"]

            if name == "spawn_teammate":
                spawned = True

            if name in self._parallel_tools:
                group = []
                while i < len(calls) and calls[i]["function"]["name"] in self._parallel_tools:
                    group.append(calls[i])
                    i += 1
                self._execute_parallel(group, messages, _disp)
            else:
                self._execute_one(tc, messages, _disp)
                i += 1

        return spawned

    def _execute_one(self, tc: dict, messages: list[dict], display=None) -> None:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
        except (json.JSONDecodeError, TypeError):
            args = {}
        logger.info(f"[工具→] {name}({json.dumps(args, ensure_ascii=False)})")
        args_summary = json.dumps(args, ensure_ascii=False)
        if display:
            display.tool_call_start(name, args_summary)
        t0 = time.monotonic()
        try:
            output = self.dispatch(name, args)
        except Exception as e:
            output = f"Error: {type(e).__name__}: {e}"
            logger.error(f"[工具✗] {name} 异常: {e}")
        elapsed = time.monotonic() - t0
        if output is not None:
            output = _truncate(output)
            logger.debug(f"[工具←] {name} len={len(output)}")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})
        if display:
            display.tool_result(name, output or "", elapsed)

    def _execute_parallel(self, calls: list[dict], messages: list[dict], display=None) -> None:
        import contextvars as _cv
        results = {}
        caller_val = _cv.copy_context()

        def _run(tc):
            try:
                name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
                logger.info(f"[并行→] {name}({json.dumps(args, ensure_ascii=False)})")
                if display:
                    display.tool_call_start(name, json.dumps(args, ensure_ascii=False))
                t0 = time.monotonic()
                result = caller_val.run(self.dispatch, name, args)
                elapsed = time.monotonic() - t0
                if display and result is not None:
                    display.tool_result(name, result, elapsed)
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

    def render_todos(self) -> str:
        return update_todos._store.render()

    def register_blackboard(self, blackboard, workflow_dirs=None):
        from . import blackboard_tools
        blackboard_tools.configure(blackboard=blackboard)
        self.add_tools(*blackboard_tools.ALL_BLACKBOARD_TOOLS)
        from . import workflow_tools
        workflow_tools.configure(blackboard=blackboard, workflow_dirs=workflow_dirs)
        self.add_tools(*workflow_tools.ALL_WORKFLOW_TOOLS)


# ── 模块级默认实例 ──

_registry = ToolRegistry()
_registry.add_tools(read_file, write_file, run_command, web_fetch, update_todos)


# ── 向后兼容的模块级函数 ──

def register(skill_loader) -> None:
    _registry.register_skills(skill_loader)


def register_subagents(subagent_loader) -> None:
    _registry.register_subagents(subagent_loader)


def register_team(bus, manager) -> None:
    _registry.register_team(bus, manager)


def register_display(display) -> None:
    _registry.register_display(display)


def register_blackboard(blackboard, workflow_dirs=None) -> None:
    _registry.register_blackboard(blackboard, workflow_dirs)


def get_definitions() -> list[dict]:
    return _registry.get_definitions()


def dispatch(name: str, args: dict) -> str | None:
    return _registry.dispatch(name, args)


def handle_tool_calls(msg: dict, messages: list[dict], display=None) -> bool:
    return _registry.handle_tool_calls(msg, messages, display)


def render_todos() -> str:
    return _registry.render_todos()
