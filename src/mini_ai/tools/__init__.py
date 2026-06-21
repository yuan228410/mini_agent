"""工具注册与分发"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..logger import logger

from . import delete_file, delete_skill, dispatch_subagent, edit_file, list_dir, read_file, read_image, rename_file, run_command, search_files, update_todos, web_fetch, write_file, config_tool, register_subagent
from .cache import ToolCache
from .catalog import BoundTool, ToolCatalog
from .dispatcher import ToolArgumentError, format_tool_exception, parse_tool_args
from .results import ToolExecutionResult
from .scheduler import plan_tool_call_segments
from ..core.execution import ExecutionBudget
from ..core.runtime_types import MessageDict, ToolArgs, ToolDefinition, ToolWirePayload
from ..core.tool_models import ToolCall, ToolResult
from ..utils import now_ts

def _truncate(output: str, max_chars: int = 8000) -> str:
    if len(output) <= max_chars:
        return output
    return output[:max_chars] + f"\n[已截断，原长 {len(output)} 字符]"


_BoundTool = BoundTool


class ToolRegistry:

    def __init__(self, *, project_path: str = "", display=None, max_result_chars: int = 8000, execution_budget: ExecutionBudget | None = None, allowed_tools: set[str] | None = None):
        self._catalog = ToolCatalog(allowed_tools=allowed_tools)
        self._display = display
        self._project_path = project_path
        self._derived_agent_resources = None
        self._cache = ToolCache(cacheable_resolver=self._is_cacheable)
        self._max_result_chars = int(max_result_chars or 8000)
        self._execution_budget = execution_budget or ExecutionBudget()

    def bind_derived_agent_resources(self, resources):
        self._derived_agent_resources = resources

    @property
    def _tools(self):
        return self._catalog.tools

    @_tools.setter
    def _tools(self, value):
        self._catalog.tools = value

    @property
    def _parallel_tools(self):
        return self._catalog.parallel_tools

    def _rebuild_index(self):
        self._catalog.rebuild_index()

    def add_tools(self, *modules):
        bound_modules = []
        for m in modules:
            if m is config_tool:
                m = _BoundTool(config_tool.definition, lambda args, _registry=self: config_tool.execute_with_registry(_registry, args))
            elif m is run_command:
                m = _BoundTool(run_command.definition, lambda args: run_command.execute_with_cwd(self._project_path or None, args))
            bound_modules.append(m)
        self._catalog.add_tools(*bound_modules)

    def register_skills(self, skill_loader):
        from . import list_skills, load_skill, install_skill, delete_skill
        self.add_tools(
            _BoundTool(list_skills.definition, lambda args, _loader=skill_loader: list_skills.list_skills_with_loader(_loader, args)),
            _BoundTool(load_skill.definition, lambda args, _loader=skill_loader: load_skill.load_skill_with_loader(_loader, args)),
            _BoundTool(install_skill.definition, lambda args, _loader=skill_loader: install_skill.install_skill_with_loader(_loader, args)),
            _BoundTool(delete_skill.definition, lambda args, _loader=skill_loader: delete_skill.delete_skill_with_loader(_loader, args)),
        )

    def _refresh_dispatch_subagent(self, subagent_loader):
        def run_dispatch(args, _loader=subagent_loader):
            resources = self._derived_agent_resources
            return dispatch_subagent.execute_with_context(
                _loader,
                args,
                project_path=self._project_path,
                display=self._display,
                registry=self,
                abort_event=getattr(resources, "abort_event", None),
                compactor=getattr(resources, "compactor", None),
                settings=getattr(resources, "settings", None),
            )

        self.add_tools(_BoundTool(
            dispatch_subagent.build_definition(subagent_loader.list_specs()),
            run_dispatch,
        ))

    def register_subagents(self, subagent_loader):
        self._refresh_dispatch_subagent(subagent_loader)
        self.add_tools(_BoundTool(
            register_subagent.definition,
            lambda args, _loader=subagent_loader: register_subagent.execute_with_context(
                _loader,
                args,
                refresh_dispatch=lambda: self._refresh_dispatch_subagent(_loader),
            ),
        ))

    def register_team(self, bus, manager):
        from . import team_tools

        def sender():
            return team_tools._sender()

        bound = [
            _BoundTool(
                team_tools._spawn_def,
                lambda args, _m=manager: team_tools.spawn_from_args(
                    _m,
                    args,
                    derived_agent_resources=self._derived_agent_resources,
                ),
            ),
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
        # workflow/subagent 工具通过 session-local bound closure 动态读取 self._display。

    def get_definitions(self) -> list[ToolDefinition]:
        return self._catalog.definitions()

    def _is_parallel_safe(self, name: str) -> bool:
        return self._catalog.is_parallel_safe(name)

    def _is_cacheable(self, name: str) -> bool:
        return self._catalog.is_cacheable(name)

    def _is_allowed(self, name: str) -> bool:
        return self._catalog.is_allowed(name)

    def dispatch_result(self, name: str, args: ToolArgs) -> ToolExecutionResult | None:
        if not self._is_allowed(name):
            return ToolExecutionResult.policy_denied(
                f"Error: 工具 '{name}' 不在当前执行策略允许范围内",
                reason="tool_not_allowed",
            )
        mod = self._catalog.get(name)
        if not mod:
            return None
        return ToolExecutionResult.from_value(mod.execute(args))

    def dispatch(self, name: str, args: ToolArgs) -> str | None:
        result = self.dispatch_result(name, args)
        return result.content if result is not None else None

    def handle_tool_calls(self, msg: MessageDict, messages: list[MessageDict], display=None, persist_fn=None) -> bool:
        calls = [ToolCall.from_dict(tc) for tc in msg["tool_calls"]]
        asst_msg = {"role": "assistant", "content": None, "tool_calls": [tc.to_dict() for tc in calls], "timestamp": now_ts()}
        messages.append(asst_msg)
        if persist_fn:
            persist_fn(asst_msg)

        _disp = display if display is not None else self._display
        spawned = False

        segments = plan_tool_call_segments(calls, self._is_parallel_safe)
        spawned = any(tc.function.name == "spawn_teammate" for segment in segments for tc in segment.calls)
        for segment in segments:
            if segment.parallel:
                self._execute_parallel(list(segment.calls), messages, _disp, persist_fn)
            else:
                for tc in segment.calls:
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
        try:
            args = parse_tool_args(tc)
        except ToolArgumentError as e:
            error_msg = e.user_message()
            tool_msg = self._tool_message(tc.id, name, error_msg)
            messages.append(tool_msg)
            self._persist_tool_message(persist_fn, tc.id, name, error_msg)
            logger.warning(f"[工具✗] {name} JSON解析失败: {e.raw_arguments[:200]}")
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
            truncated = _truncate(full_output, self._max_result_chars)
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
            execution_result = self.dispatch_result(name, args)
            output = execution_result.content if execution_result is not None else None
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
            output = format_tool_exception(name, args_summary, e)
            logger.error(f"[工具✗] {name} 异常: {e}", exc_info=True)
        elapsed = time.monotonic() - t0
        if display:
            display.tool_result(name, output or "", elapsed, tc.id)

        # OpenAI 规范：assistant.tool_calls 中的每个 tool_call_id 都必须有
        # 对应的 role=tool 消息响应。即使工具返回 None（或执行成功但无输出），
        # 也必须写入一条占位 tool 消息，否则下次 LLM 调用会 HTTP 400
        full_output = output if output is not None else ""
        logger.debug(f"[工具←] {name} len={len(full_output)}")
        truncated = _truncate(full_output, self._max_result_chars)
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
                try:
                    args = parse_tool_args(tc)
                except ToolArgumentError as e:
                    return tc.id, e.user_message()

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
                execution_result = ctx_copy.run(self.dispatch_result, name, args)
                result = execution_result.content if execution_result is not None else None
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
                args_summary = json.dumps(args, ensure_ascii=False) if 'args' in locals() else "{}"
                tool_name = name if 'name' in locals() else 'unknown'
                error_msg = format_tool_exception(tool_name, args_summary, e)

                logger.error(f"[并行工具✗] {tool_name} 异常: {e}", exc_info=True)
                
                # 异常时也要推送 tool_result，避免前端占位符不更新
                if display:
                    elapsed = time.monotonic() - t0 if 't0' in locals() else 0
                    display.tool_result(name if 'name' in locals() else 'unknown', error_msg, elapsed, tc.id)
                
                return tc.id, error_msg

        max_workers = min(len(calls), max(1, self._execution_budget.max_parallel_tools))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
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
            truncated = _truncate(full_output, self._max_result_chars)
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
                derived_agent_resources=self._derived_agent_resources,
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
        self.add_tools(
            _BoundTool(memory_tools._remember_def, lambda args, _store=memory_store: memory_tools.remember_with_store(_store, args)),
            _BoundTool(memory_tools._recall_def, lambda args, _store=memory_store: memory_tools.recall_with_store(_store, args)),
            _BoundTool(memory_tools._forget_def, lambda args, _store=memory_store: memory_tools.forget_with_store(_store, args)),
        )

    def register_history_tools(self, history_db, workspace: str = "default"):
        from . import history_tools
        self.add_tools(
            _BoundTool(history_tools._search_def, lambda args, _db=history_db, _workspace=workspace: history_tools.search_history_with_db(_db, _workspace, args)),
            _BoundTool(history_tools._manage_def, lambda args, _db=history_db, _workspace=workspace: history_tools.manage_history_with_db(_db, _workspace, args)),
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
