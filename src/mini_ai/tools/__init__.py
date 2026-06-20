"""工具注册与分发"""
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import TOOL
from datetime import datetime, timezone, timedelta
from ..logger import logger

from . import delete_file, delete_skill, dispatch_subagent, edit_file, list_dir, read_file, read_image, rename_file, run_command, search_files, update_todos, web_fetch, write_file, config_tool, register_subagent
from .cache import ToolCache, get_tool_cache
from .metadata import metadata_for, normalize_tool_definition
from ..core.runtime_types import MessageDict, ToolArgs, ToolDefinition, ToolWirePayload
from ..core.tool_models import ToolCall, ToolResult
from ..utils import now_ts

_MAX_RESULT_CHARS = TOOL.get("max_result_chars", 8000)

def _truncate(output: str) -> str:
    if len(output) <= _MAX_RESULT_CHARS:
        return output
    return output[:_MAX_RESULT_CHARS] + f"\n[已截断，原长 {len(output)} 字符]"


class _BoundTool:
    """Small module-like wrapper binding a tool definition to a closure."""

    def __init__(self, definition: ToolDefinition, execute: Callable[[ToolArgs], str], metadata=None):
        self.definition = definition
        self.execute = execute
        self.metadata = metadata


def _run_with_context(var_values, fn: Callable[[ToolArgs], str], args: ToolArgs) -> str:
    """Run a legacy contextvar-based tool with session-local bindings."""
    tokens = []
    try:
        for var, value in var_values:
            tokens.append((var, var.set(value)))
        return fn(args)
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class ToolRegistry:

    def __init__(self):
        self._tools: list = []
        self._by_name: dict[str, object] = {}
        self._display = None
        self._project_path = ""
        self._tool_metadata: dict[str, object] = {}
        self._cache = ToolCache(cacheable_resolver=self._is_cacheable)
        # 兼容测试/外部扩展；新代码优先通过 ToolMetadata.parallel_safe 声明。
        self._parallel_tools: set[str] = set()

    def _normalize_module_definition(self, module) -> ToolDefinition:
        raw = module.definition() if callable(getattr(module, "definition", None)) else module.definition
        meta = metadata_for(raw.get("function", {}).get("name", ""), getattr(module, "metadata", None))
        normalized = normalize_tool_definition(raw, meta)
        module.definition = normalized
        self._tool_metadata[normalized["function"]["name"]] = meta
        return normalized

    def _rebuild_index(self):
        self._by_name.clear()
        self._by_name.update({m.definition["function"]["name"]: m for m in self._tools})

    def add_tools(self, *modules):
        existing = {m.definition["function"]["name"] for m in self._tools}
        for m in modules:
            definition = self._normalize_module_definition(m)
            name = definition["function"]["name"]
            if name not in existing:
                self._tools.append(m)
                existing.add(name)
            else:
                for i, old in enumerate(self._tools):
                    if old.definition["function"]["name"] == name:
                        self._tools[i] = m
                        break
        self._rebuild_index()

    def register_skills(self, skill_loader):
        from . import list_skills, load_skill, install_skill, delete_skill
        list_skills.configure(loader=skill_loader)
        load_skill.configure(loader=skill_loader)
        install_skill.configure(loader=skill_loader)
        delete_skill.configure(loader=skill_loader)
        self.add_tools(
            _BoundTool(list_skills.definition, lambda args, _m=list_skills: _run_with_context([(_m._loader_var, skill_loader)], _m.execute, args)),
            _BoundTool(load_skill.definition, lambda args, _m=load_skill: _run_with_context([(_m._loader_var, skill_loader)], _m.execute, args)),
            _BoundTool(install_skill.definition, lambda args, _m=install_skill: _run_with_context([(_m._loader_var, skill_loader)], _m.execute, args)),
            _BoundTool(delete_skill.definition, lambda args, _m=delete_skill: _run_with_context([(_m._loader_var, skill_loader)], _m.execute, args)),
        )

    def register_subagents(self, subagent_loader):
        subagent_list = subagent_loader.list_specs()
        dispatch_subagent.configure(
            loader=subagent_loader,
            definition=dispatch_subagent.build_definition(subagent_list),
            project_path=self._project_path,
            display=self._display,
            registry=self,
        )
        self.add_tools(_BoundTool(
            dispatch_subagent.definition,
            lambda args, _m=dispatch_subagent: _run_with_context([
                (_m._project_path_ctx, self._project_path),
                (_m._display_ctx, self._display),
                (_m._registry_ctx, self),
            ], _m.execute, args),
        ))
        register_subagent.configure(loader=subagent_loader, registry=self)
        self.add_tools(_BoundTool(
            register_subagent.definition,
            lambda args, _m=register_subagent: _run_with_context([(_m._registry_ctx, self)], _m.execute, args),
        ))

    def register_team(self, bus, manager):
        from . import team_tools
        team_tools.configure(bus=bus, manager=manager)  # legacy callers still rely on module functions
        team_tools.set_caller("lead")

        def sender():
            return team_tools._sender()

        bound = [
            _BoundTool(team_tools._spawn_def, lambda args, _m=manager: _m.spawn(team_tools._arg_text(args, "name"), team_tools._arg_text(args, "role"), team_tools._arg_text(args, "prompt"))),
            _BoundTool(team_tools._list_def, lambda args, _m=manager: _m.list_all()),
            _BoundTool(team_tools._send_def, lambda args, _b=bus: team_tools.send_from_args(_b, sender(), args)),
            _BoundTool(team_tools._read_def, lambda args, _b=bus: json.dumps(_b.read_inbox(sender()), ensure_ascii=False, indent=2)),
            _BoundTool(team_tools._broadcast_def, lambda args, _b=bus, _m=manager: team_tools.broadcast_from_args(_b, _m, sender(), args)),
        ]

        def dismiss(args, _b=bus, _m=manager):
            return team_tools.dismiss_team(_b, _m)

        bound.append(_BoundTool(team_tools._dismiss_def, dismiss))
        self.add_tools(*bound)

    def register_display(self, display):
        self._display = display
        try:
            dispatch_subagent.configure(display=display, registry=self)
            if "dispatch_subagent" in self._by_name:
                self.register_subagents(dispatch_subagent._loader)
            # workflow 工具通过 session-local bound closure 动态读取 self._display。
        except Exception:
            pass

    def get_definitions(self) -> list[ToolDefinition]:
        return [json.loads(json.dumps(m.definition, ensure_ascii=False)) for m in self._tools]

    def _is_parallel_safe(self, name: str) -> bool:
        return name in self._parallel_tools or bool(metadata_for(name, self._tool_metadata.get(name)).parallel_safe)

    def _is_cacheable(self, name: str) -> bool:
        return bool(metadata_for(name, self._tool_metadata.get(name)).cacheable)

    def dispatch(self, name: str, args: ToolArgs) -> str | None:
        mod = self._by_name.get(name)
        if not mod:
            return None
        if mod is config_tool:
            return _run_with_context([(config_tool._registry_ctx, self)], mod.execute, args)
        return mod.execute(args)

    def handle_tool_calls(self, msg: MessageDict, messages: list[MessageDict], display=None, persist_fn=None) -> bool:
        calls = [ToolCall.from_dict(tc) for tc in msg["tool_calls"]]
        asst_msg = {"role": "assistant", "content": None, "tool_calls": [tc.to_dict() for tc in calls], "timestamp": now_ts()}
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
            name = tc.function.name
            if name == "spawn_teammate":
                spawned = True
            if self._is_parallel_safe(name):
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

    def _as_tool_call(self, tc: ToolCall | ToolWirePayload) -> ToolCall:
        return tc if isinstance(tc, ToolCall) else ToolCall.from_dict(tc)

    def _tool_message(self, tool_call_id: str, name: str, content: str) -> MessageDict:
        return ToolResult(tool_call_id=tool_call_id, name=name, content=content).to_message(timestamp=now_ts())

    def _persist_tool_message(self, persist_fn, tool_call_id: str, name: str, content: str) -> None:
        if persist_fn:
            persist_fn(self._tool_message(tool_call_id, name, content))

    def _execute_one(self, tc: ToolCall | ToolWirePayload, messages: list[MessageDict], display=None, persist_fn=None) -> None:
        tc = self._as_tool_call(tc)
        name = tc.function.name
        raw_args = tc.function.arguments
        try:
            args = json.loads(raw_args) if raw_args else {}
        except (json.JSONDecodeError, TypeError) as e:
            # JSON 解析失败，返回详细错误给 LLM
            args = {}
            error_msg = f"⚠ 工具调用失败：参数 JSON 解析错误\n\n工具: {name}\n原始参数: {raw_args[:200]}\n错误: {type(e).__name__}: {e}\n\n请检查参数格式是否正确。"
            tool_msg = self._tool_message(tc.id, name, error_msg)
            messages.append(tool_msg)
            self._persist_tool_message(persist_fn, tc.id, name, error_msg)
            logger.warning(f"[工具✗] {name} JSON解析失败: {raw_args[:200]}")
            return
        
        # 检查会话级缓存
        cache = self._cache
        cached_result, hit = cache.get(name, args)
        
        if hit:
            logger.info(f"[缓存命中] {name}")
            output = cached_result
            # 缓存命中也需要写入 messages，否则 LLM 无法获取工具结果
            # 即使 output 为 None 也必须写 tool 消息，否则 OpenAI API 会因
            # assistant.tool_calls 未被一一响应而返回 HTTP 400
            full_output = output if output is not None else ""
            logger.debug(f"[工具←] {name} (cached) len={len(full_output)}")
            truncated = _truncate(full_output)
            tool_msg = self._tool_message(tc.id, name, truncated)
            self._persist_tool_message(persist_fn, tc.id, name, full_output)
            messages.append(tool_msg)
            if display:
                display.tool_result(name, full_output, 0, tc.id)  # elapsed=0 for cache hit
            return  # 缓存命中直接返回
        
        logger.info(f"[工具→] {name}({json.dumps(args, ensure_ascii=False)})")
        args_summary = json.dumps(args, ensure_ascii=False)
        if display:
            display.tool_call_start(name, args_summary, tc.id)
        t0 = time.monotonic()
        try:
            output = self.dispatch(name, args)
            if name == "update_todos" and display and output and not output.startswith("Error:"):
                display.todos_updated(output)
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
            display.tool_result(name, output or "", elapsed, tc.id)

        # OpenAI 规范：assistant.tool_calls 中的每个 tool_call_id 都必须有
        # 对应的 role=tool 消息响应。即使工具返回 None（或执行成功但无输出），
        # 也必须写入一条占位 tool 消息，否则下次 LLM 调用会 HTTP 400
        full_output = output if output is not None else ""
        logger.debug(f"[工具←] {name} len={len(full_output)}")
        truncated = _truncate(full_output)
        tool_msg = self._tool_message(tc.id, name, truncated)
        self._persist_tool_message(persist_fn, tc.id, name, full_output)
        messages.append(tool_msg)

    def _execute_parallel(self, calls: list[ToolCall | ToolWirePayload], messages: list[MessageDict], display=None, persist_fn=None) -> None:
        calls = [self._as_tool_call(tc) for tc in calls]
        results = {}
        cache = self._cache

        # 在主线程为每个并行任务各捕获一份上下文副本（Context.run 不可并发进入同一对象）
        import contextvars as _cv
        _ctx_copies = [_cv.copy_context() for _ in calls]

        def _run(tc: ToolCall, ctx_copy):
            try:
                name = tc.function.name
                args_str = tc.function.arguments or ""
                
                # JSON 解析
                try:
                    args = json.loads(args_str) if args_str else {}
                except (json.JSONDecodeError, TypeError) as e:
                    error_msg = f"⚠ 工具调用失败：参数 JSON 解析错误\n\n工具: {name}\n原始参数: {args_str[:200]}\n错误: {type(e).__name__}: {e}\n\n请检查参数格式是否正确。"
                    return tc.id, error_msg
                
                # 检查缓存（并发安全：首个线程执行，其余等待）
                cached_result, hit = cache.get_or_wait(name, args)
                if hit:
                    logger.info(f"[并行缓存命中] {name}")
                    # 缓存命中也需要推送 tool_result
                    if display and cached_result is not None:
                        display.tool_result(name, cached_result, 0, tc.id)
                    return tc.id, cached_result
                
                logger.info(f"[并行→] {name}({json.dumps(args, ensure_ascii=False)})")
                if display:
                    display.tool_call_start(name, json.dumps(args, ensure_ascii=False), tc.id)
                t0 = time.monotonic()
                result = ctx_copy.run(self.dispatch, name, args)
                elapsed = time.monotonic() - t0
                if name == "update_todos" and display and result and not result.startswith("Error:"):
                    display.todos_updated(result)
                if display and result is not None:
                    display.tool_result(name, result, elapsed, tc.id)
                # 写入缓存并通知等待线程
                cache.mark_done(name, args, result)
                # 🔧 新增：打印工具执行结果摘要
                if result:
                    result_preview = result[:200] if len(result) > 200 else result
                    logger.info(f"[并行←] {name} len={len(result)} preview={result_preview}")
                else:
                    logger.info(f"[并行←] {name} len=0 output=None")
                return tc.id, result
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
                    display.tool_result(name if 'name' in locals() else 'unknown', error_msg, elapsed, tc.id)
                
                return tc.id, error_msg

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = {pool.submit(_run, tc, _ctx_copies[i]): i for i, tc in enumerate(calls)}
            for future in as_completed(futures):
                try:
                    tc_id, output = future.result()
                    # 即使 output 为 None 也要记录，保证每个 tool_call_id 都有响应
                    # 否则 OpenAI API 会 HTTP 400（insufficient tool messages）
                    results[tc_id] = output if output is not None else ""
                except Exception as e:
                    logger.error(f"[并行✗] 获取结果异常: {e}")

        # 每个 tool_call_id 都必须写入对应 tool 消息
        for tc in calls:
            tc_id = tc.id
            full_output = results.get(tc_id, "")
            name = tc.function.name
            truncated = _truncate(full_output)
            tool_msg = self._tool_message(tc_id, name, truncated)
            self._persist_tool_message(persist_fn, tc_id, name, full_output)
            messages.append(tool_msg)

    def render_todos(self) -> str:
        return update_todos._store.render()

    def register_blackboard(self, blackboard, workflow_dirs=None, bus=None, manager=None):
        from . import blackboard_tools, workflow_tools
        from .team_tools import _sender
        import threading as _threading

        self.add_tools(
            _BoundTool(blackboard_tools._write_def, lambda args, _bb=blackboard: blackboard_tools.write_to_blackboard(_bb, args, author=_sender())),
            _BoundTool(blackboard_tools._read_def, lambda args, _bb=blackboard: blackboard_tools.read_from_blackboard(_bb, args)),
            _BoundTool(blackboard_tools._list_def, lambda args, _bb=blackboard: blackboard_tools.list_blackboard_keys(_bb, args)),
        )

        graphs: workflow_tools.WorkflowGraphStore = {}
        graphs_lock = _threading.Lock()
        workflow_dirs = workflow_dirs or []

        def run_workflow(args, _bb=blackboard, _bus=bus, _manager=manager, _graphs=graphs):
            return workflow_tools.run_workflow_with_context(
                args,
                blackboard=_bb,
                graphs=_graphs,
                bus=_bus,
                manager=_manager,
                display=self._display,
            )

        def workflow_status(args, _graphs=graphs):
            return workflow_tools.workflow_status_from_graphs(_graphs, graphs_lock)

        def load_workflow(args, _dirs=workflow_dirs):
            return workflow_tools.load_workflow_from_dirs(args, _dirs)

        self.add_tools(
            _BoundTool(workflow_tools._run_def, run_workflow),
            _BoundTool(workflow_tools._status_def, workflow_status),
            _BoundTool(workflow_tools._load_def, load_workflow),
        )

    def register_memory_tools(self, memory_store):
        from . import memory_tools
        memory_tools.configure(memory_store=memory_store)
        self.add_tools(
            _BoundTool(memory_tools._remember_def, lambda args, _m=memory_tools: _run_with_context([(_m._memory_store, memory_store)], _m._remember_exec, args)),
            _BoundTool(memory_tools._recall_def, lambda args, _m=memory_tools: _run_with_context([(_m._memory_store, memory_store)], _m._recall_exec, args)),
            _BoundTool(memory_tools._forget_def, lambda args, _m=memory_tools: _run_with_context([(_m._memory_store, memory_store)], _m._forget_exec, args)),
        )

    def register_history_tools(self, history_db, workspace: str = "default"):
        from . import history_tools
        history_tools.configure(history_db=history_db, workspace=workspace)
        bindings = [(history_tools._history_db, history_db), (history_tools._current_workspace, workspace)]
        self.add_tools(
            _BoundTool(history_tools._search_def, lambda args, _m=history_tools: _run_with_context(bindings, _m._search_exec, args)),
            _BoundTool(history_tools._manage_def, lambda args, _m=history_tools: _run_with_context(bindings, _m._manage_exec, args)),
        )

# ── 禁止运行时模块级 registry fallback ──

_GLOBAL_REGISTRY_ERROR = "模块级 ToolRegistry 已禁用；请使用 session-local ToolRegistry 或 build_tool_registry()"


def _raise_global_registry_error():
    raise RuntimeError(_GLOBAL_REGISTRY_ERROR)


# 这些函数保留名称以便错误显式暴露，但内部运行路径不得依赖它们。
def register(skill_loader) -> None:
    _raise_global_registry_error()


def register_subagents(subagent_loader) -> None:
    _raise_global_registry_error()


def set_project_path(path: str) -> None:
    _raise_global_registry_error()


def register_team(bus, manager) -> None:
    _raise_global_registry_error()


def register_display(display) -> None:
    _raise_global_registry_error()


def register_blackboard(blackboard, workflow_dirs=None, bus=None, manager=None) -> None:
    _raise_global_registry_error()


def register_memory_tools(memory_store) -> None:
    _raise_global_registry_error()


def register_history_tools(history_db, workspace: str = "default") -> None:
    _raise_global_registry_error()


def get_definitions() -> list[ToolDefinition]:
    _raise_global_registry_error()


def dispatch(name: str, args: ToolArgs) -> str | None:
    _raise_global_registry_error()


def handle_tool_calls(msg: MessageDict, messages: list[MessageDict], display=None, persist_fn=None) -> bool:
    _raise_global_registry_error()


def render_todos() -> str:
    from .update_todos import render_current_todos
    return render_current_todos()


def inject_todos(messages: list[MessageDict]) -> None:
    """将当前任务计划注入 system prompt 的尾部（供 main.py 和 chat.py 共用）"""
    from .update_todos import inject_todos as _impl
    _impl(messages)
