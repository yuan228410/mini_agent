"""工具注册与分发"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import TOOL
from datetime import datetime, timezone, timedelta
from ..logger import logger

from . import delete_file, delete_skill, dispatch_subagent, edit_file, list_dir, read_file, read_image, rename_file, run_command, search_files, update_todos, web_fetch, write_file, config_tool, register_subagent
from .cache import get_tool_cache
from ..utils import now_ts

_MAX_RESULT_CHARS = TOOL.get("max_result_chars", 8000)

def _truncate(output: str) -> str:
    if len(output) <= _MAX_RESULT_CHARS:
        return output
    return output[:_MAX_RESULT_CHARS] + f"\n[已截断，原长 {len(output)} 字符]"

class ToolRegistry:

    def __init__(self):
        self._tools: list = []
        self._by_name: dict[str, object] = {}
        self._display = None
        self._project_path = ""
        # 并行执行的工具白名单（不含写操作工具，避免文件竞态）
        self._parallel_tools: set[str] = {
            "dispatch_subagent", "spawn_teammate",
            "read_file", "search_files", "list_dir",
            "web_fetch", "list_skills", "load_skill",
            "recall", "search_history",
            "delete_skill",
        }

    def _rebuild_index(self):
        self._by_name.clear()
        self._by_name.update({m.definition["function"]["name"]: m for m in self._tools})

    def add_tools(self, *modules):
        existing = {m.definition["function"]["name"] for m in self._tools}
        for m in modules:
            if m.definition["function"]["name"] not in existing:
                self._tools.append(m)
                existing.add(m.definition["function"]["name"])
        self._rebuild_index()

    def register_skills(self, skill_loader):
        from . import list_skills, load_skill, install_skill, delete_skill
        list_skills.configure(loader=skill_loader)
        load_skill.configure(loader=skill_loader)
        install_skill.configure(loader=skill_loader)
        delete_skill.configure(loader=skill_loader)
        self.add_tools(list_skills, load_skill, install_skill, delete_skill)

    def register_subagents(self, subagent_loader):
        subagent_list = subagent_loader.list_specs()
        dispatch_subagent.configure(
            loader=subagent_loader,
            definition=dispatch_subagent.build_definition(subagent_list),
            project_path=self._project_path,
        )
        self.add_tools(dispatch_subagent)
        register_subagent.configure(loader=subagent_loader)
        self.add_tools(register_subagent)

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

    def handle_tool_calls(self, msg: dict, messages: list[dict], display=None, persist_fn=None) -> bool:
        calls = msg["tool_calls"]
        asst_msg = {"role": "assistant", "content": None, "tool_calls": calls, "timestamp": now_ts()}
        messages.append(asst_msg)
        if persist_fn:
            persist_fn(asst_msg)

        _disp = display if display is not None else self._display
        spawned = False

        # 优化：先收集所有并行工具，再一次性执行
        # 避免连续并行工具被串行工具打断
        parallel_calls = []
        serial_calls = []
        
        for tc in calls:
            name = tc["function"]["name"]
            if name == "spawn_teammate":
                spawned = True
            if name in self._parallel_tools:
                parallel_calls.append(tc)
            else:
                serial_calls.append(tc)

        # 先执行所有并行工具
        if len(parallel_calls) > 1:
            self._execute_parallel(parallel_calls, messages, _disp, persist_fn)
        elif len(parallel_calls) == 1:
            self._execute_one(parallel_calls[0], messages, _disp, persist_fn)
        
        # 再执行串行工具
        for tc in serial_calls:
            self._execute_one(tc, messages, _disp, persist_fn)

        return spawned

    def _execute_one(self, tc: dict, messages: list[dict], display=None, persist_fn=None) -> None:
        name = tc["function"]["name"]
        raw_args = tc["function"].get("arguments", "")
        try:
            args = json.loads(raw_args) if raw_args else {}
        except (json.JSONDecodeError, TypeError) as e:
            # JSON 解析失败，返回详细错误给 LLM
            args = {}
            error_msg = f"⚠ 工具调用失败：参数 JSON 解析错误\n\n工具: {name}\n原始参数: {raw_args[:200]}\n错误: {type(e).__name__}: {e}\n\n请检查参数格式是否正确。"
            tool_msg = {"role": "tool", "tool_call_id": tc["id"], "name": name, "content": error_msg, "timestamp": now_ts()}
            messages.append(tool_msg)
            logger.warning(f"[工具✗] {name} JSON解析失败: {raw_args[:200]}")
            return
        
        # 检查缓存
        cache = get_tool_cache()
        cached_result, hit = cache.get(name, args)
        
        if hit:
            logger.info(f"[缓存命中] {name}")
            output = cached_result
            # 缓存命中也需要写入 messages，否则 LLM 无法获取工具结果
            if output is not None:
                full_output = output
                logger.debug(f"[工具←] {name} (cached) len={len(output)}")
                truncated = _truncate(output)
                tool_msg = {"role": "tool", "tool_call_id": tc["id"], "name": name, "content": truncated, "timestamp": now_ts()}
                if persist_fn:
                    persist_fn({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": full_output, "timestamp": now_ts()})
                messages.append(tool_msg)
                if display:
                    display.tool_result(name, output, 0, tc["id"])  # elapsed=0 for cache hit
            return  # 缓存命中直接返回
        
        logger.info(f"[工具→] {name}({json.dumps(args, ensure_ascii=False)})")
        args_summary = json.dumps(args, ensure_ascii=False)
        if display:
            display.tool_call_start(name, args_summary, tc["id"])
        t0 = time.monotonic()
        try:
            output = self.dispatch(name, args)
            # 写入缓存
            cache.set(name, args, output)
            # 🔧 新增：打印工具执行结果摘要
            if output:
                output_preview = output[:200] if len(output) > 200 else output
                logger.info(f"[工具←] {name} len={len(output)} preview={output_preview}")
            else:
                logger.info(f"[工具←] {name} len=0 output=None")
        except Exception as e:
            # 使用异常体系生成详细错误信息
            from ..exceptions import MiniAIError, ToolError
            if isinstance(e, MiniAIError):
                # 已知异常，提供详细上下文
                output = f"⚠ {e.to_user_message()}\n\n工具: {name}\n参数: {args_summary}\n错误类型: {type(e).__name__}\n可恢复: {'是' if e.recoverable else '否'}"
                if hasattr(e, 'context') and e.context:
                    output += f"\n上下文: {e.context}"
            elif isinstance(e, (FileNotFoundError, PermissionError, IsADirectoryError)):
                # 文件系统异常，提供具体信息
                output = f"⚠ 文件操作失败\n\n工具: {name}\n错误: {type(e).__name__}: {e}\n参数: {args_summary}\n\n请检查路径是否正确，权限是否足够。"
            else:
                # 未知异常，提供完整堆栈
                import traceback
                output = f"⚠ 工具执行异常\n\n工具: {name}\n错误: {type(e).__name__}: {e}\n参数: {args_summary}\n\n堆栈:\n{traceback.format_exc()}"
            logger.error(f"[工具✗] {name} 异常: {e}", exc_info=True)
        elapsed = time.monotonic() - t0
        if display:
            display.tool_result(name, output or "", elapsed, tc["id"])
        
        if output is not None:
            full_output = output
            logger.debug(f"[工具←] {name} len={len(output)}")
            truncated = _truncate(output)
            tool_msg = {"role": "tool", "tool_call_id": tc["id"], "name": name, "content": truncated, "timestamp": now_ts()}
            if persist_fn:
                persist_fn({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": full_output, "timestamp": now_ts()})
            messages.append(tool_msg)

    def _execute_parallel(self, calls: list[dict], messages: list[dict], display=None, persist_fn=None) -> None:
        results = {}
        cache = get_tool_cache()

        # 在主线程为每个并行任务各捕获一份上下文副本（Context.run 不可并发进入同一对象）
        import contextvars as _cv
        _ctx_copies = [_cv.copy_context() for _ in calls]

        def _run(tc, ctx_copy):
            try:
                name = tc["function"]["name"]
                args_str = tc["function"]["arguments"] or ""
                
                # JSON 解析
                try:
                    args = json.loads(args_str) if args_str else {}
                except (json.JSONDecodeError, TypeError) as e:
                    error_msg = f"⚠ 工具调用失败：参数 JSON 解析错误\n\n工具: {name}\n原始参数: {args_str[:200]}\n错误: {type(e).__name__}: {e}\n\n请检查参数格式是否正确。"
                    return tc["id"], error_msg
                
                # 检查缓存（并发安全：首个线程执行，其余等待）
                cached_result, hit = cache.get_or_wait(name, args)
                if hit:
                    logger.info(f"[并行缓存命中] {name}")
                    # 缓存命中也需要推送 tool_result
                    if display and cached_result is not None:
                        display.tool_result(name, cached_result, 0, tc["id"])
                    return tc["id"], cached_result
                
                logger.info(f"[并行→] {name}({json.dumps(args, ensure_ascii=False)})")
                if display:
                    display.tool_call_start(name, json.dumps(args, ensure_ascii=False), tc["id"])
                t0 = time.monotonic()
                result = ctx_copy.run(self.dispatch, name, args)
                elapsed = time.monotonic() - t0
                if display and result is not None:
                    display.tool_result(name, result, elapsed, tc["id"])
                # 写入缓存并通知等待线程
                cache.mark_done(name, args, result)
                # 🔧 新增：打印工具执行结果摘要
                if result:
                    result_preview = result[:200] if len(result) > 200 else result
                    logger.info(f"[并行←] {name} len={len(result)} preview={result_preview}")
                else:
                    logger.info(f"[并行←] {name} len=0 output=None")
                return tc["id"], result
            except Exception as e:
                # 使用异常体系生成详细错误信息
                from ..exceptions import MiniAIError
                args_summary = json.dumps(args, ensure_ascii=False) if 'args' in locals() else "{}"
                
                if isinstance(e, MiniAIError):
                    error_msg = f"⚠ {e.to_user_message()}\n\n工具: {name}\n参数: {args_summary}\n错误类型: {type(e).__name__}\n可恢复: {'是' if e.recoverable else '否'}"
                    if hasattr(e, 'context') and e.context:
                        error_msg += f"\n上下文: {e.context}"
                elif isinstance(e, (FileNotFoundError, PermissionError, IsADirectoryError)):
                    error_msg = f"⚠ 文件操作失败\n\n工具: {name}\n错误: {type(e).__name__}: {e}\n参数: {args_summary}\n\n请检查路径是否正确，权限是否足够。"
                else:
                    import traceback
                    error_msg = f"⚠ 工具执行异常\n\n工具: {name if 'name' in locals() else 'unknown'}\n错误: {type(e).__name__}: {e}\n参数: {args_summary}\n\n堆栈:\n{traceback.format_exc()}"
                
                logger.error(f"[并行工具✗] {name if 'name' in locals() else 'unknown'} 异常: {e}", exc_info=True)
                
                # 异常时也要推送 tool_result，避免前端占位符不更新
                if display:
                    elapsed = time.monotonic() - t0 if 't0' in locals() else 0
                    display.tool_result(name if 'name' in locals() else 'unknown', error_msg, elapsed, tc["id"])
                
                return tc["id"], error_msg

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = {pool.submit(_run, tc, _ctx_copies[i]): i for i, tc in enumerate(calls)}
            for future in as_completed(futures):
                try:
                    tc_id, output = future.result()
                    if output is not None:
                        results[tc_id] = output
                except Exception as e:
                    logger.error(f"[并行✗] 获取结果异常: {e}")

        for tc in calls:
            if tc["id"] in results:
                full_output = results[tc["id"]]
                truncated = _truncate(full_output)
                tool_msg = {"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"], "content": truncated, "timestamp": now_ts()}
                if persist_fn:
                    persist_fn({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"], "content": full_output, "timestamp": now_ts()})
                messages.append(tool_msg)

    def render_todos(self) -> str:
        return update_todos._store.render()

    def register_blackboard(self, blackboard, workflow_dirs=None, bus=None, manager=None):
        from . import blackboard_tools
        blackboard_tools.configure(blackboard=blackboard)
        self.add_tools(*blackboard_tools.ALL_BLACKBOARD_TOOLS)
        from . import workflow_tools
        workflow_tools.configure(blackboard=blackboard, workflow_dirs=workflow_dirs, bus=bus, manager=manager)
        self.add_tools(*workflow_tools.ALL_WORKFLOW_TOOLS)

    def register_memory_tools(self, memory_store):
        from . import memory_tools
        memory_tools.configure(memory_store=memory_store)
        self.add_tools(*memory_tools.ALL_MEMORY_TOOLS)

    def register_history_tools(self, history_db, workspace: str = "default"):
        from . import history_tools
        history_tools.configure(history_db=history_db, workspace=workspace)
        self.add_tools(*history_tools.ALL_HISTORY_TOOLS)

# ── 模块级默认实例 ──

_registry = ToolRegistry()
_registry.add_tools(read_file, read_image, write_file, edit_file, delete_file, rename_file, run_command, search_files, list_dir, web_fetch, update_todos, config_tool, delete_skill)

# ── 向后兼容的模块级函数 ──

def register(skill_loader) -> None:
    _registry.register_skills(skill_loader)

def register_subagents(subagent_loader) -> None:
    _registry.register_subagents(subagent_loader)

def set_project_path(path: str) -> None:
    from . import dispatch_subagent
    _registry._project_path = path
    dispatch_subagent.configure(project_path=path)

def register_team(bus, manager) -> None:
    _registry.register_team(bus, manager)

def register_display(display) -> None:
    _registry.register_display(display)

def register_blackboard(blackboard, workflow_dirs=None, bus=None, manager=None) -> None:
    _registry.register_blackboard(blackboard, workflow_dirs, bus=bus, manager=manager)

def register_memory_tools(memory_store) -> None:
    _registry.register_memory_tools(memory_store)

def register_history_tools(history_db, workspace: str = "default") -> None:
    _registry.register_history_tools(history_db, workspace)

def get_definitions() -> list[dict]:
    return _registry.get_definitions()

def dispatch(name: str, args: dict) -> str | None:
    return _registry.dispatch(name, args)

def handle_tool_calls(msg: dict, messages: list[dict], display=None, persist_fn=None) -> bool:
    return _registry.handle_tool_calls(msg, messages, display, persist_fn)

def render_todos() -> str:
    return _registry.render_todos()


def inject_todos(messages: list[dict]):
    """将当前任务计划注入 system prompt 的尾部（供 main.py 和 chat.py 共用）"""
    from .update_todos import inject_todos as _impl
    _impl(messages)
