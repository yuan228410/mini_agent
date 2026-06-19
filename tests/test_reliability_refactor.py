import inspect
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from mini_ai.core.persister import HistoryPersister
from mini_ai.memory.async_db_writer import AsyncDBWriter
from mini_ai.memory.history_db import HistoryDB
from mini_ai.tools import ToolRegistry
from mini_ai.tools.metadata import normalize_tool_definition
from mini_ai.web.display import WebDisplay
from mini_ai.config import RequestContext
from mini_ai.tools import config_tool


class SlowCommitWriter(AsyncDBWriter):
    def __init__(self, *args, entered: threading.Event, release: threading.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.entered = entered
        self.release = release

    def _do_write_batch(self, tasks):
        self.entered.set()
        self.release.wait(timeout=2)
        return super()._do_write_batch(tasks)


def _init_schema(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace TEXT NOT NULL,
            session_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            metadata TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content=messages, content_rowid=id)")
    conn.close()


def test_async_db_writer_flush_waits_for_inflight_commit(tmp_path):
    db_path = tmp_path / "flush.db"
    _init_schema(db_path)
    entered = threading.Event()
    release = threading.Event()
    writer = SlowCommitWriter(db_path, entered=entered, release=release)
    writer.start()
    writer.submit_write("ws", "sid", "user", "hello")
    assert entered.wait(timeout=1)

    result = {"done": False, "error": None}

    def do_flush():
        try:
            writer.flush(timeout=2)
            result["done"] = True
        except Exception as exc:  # pragma: no cover - asserted below
            result["error"] = exc

    t = threading.Thread(target=do_flush)
    t.start()
    time.sleep(0.1)
    assert result["done"] is False
    release.set()
    t.join(timeout=2)
    assert result == {"done": True, "error": None}
    writer.stop()


def test_request_context_closes_owned_session():
    class FakeSession:
        closed = False
        def close(self):
            self.closed = True

    session = FakeSession()
    ctx = RequestContext({"api_key": "k"}, http_session=session)
    ctx.close()
    assert session.closed is False

    owned = RequestContext({"api_key": "k"})
    owned.close()


def test_tool_registry_definitions_are_copied_and_metadata_drives_parallel():
    class Tool:
        definition = {
            "type": "function",
            "function": {"name": "read_file", "description": "", "parameters": {"type": "object", "properties": {}}},
        }
        @staticmethod
        def execute(args):
            return "ok"

    registry = ToolRegistry()
    registry.add_tools(Tool)
    defs = registry.get_definitions()
    defs[0]["function"]["name"] = "mutated"
    assert registry.get_definitions()[0]["function"]["name"] == "read_file"
    assert registry._is_parallel_safe("read_file") is True


def test_tool_registry_cache_is_local_and_metadata_gated():
    counters = {"cacheable": 0, "stateful": 0}

    class CacheableTool:
        definition = {
            "type": "function",
            "function": {"name": "read_file", "description": "", "parameters": {"type": "object", "properties": {}}},
        }
        @staticmethod
        def execute(args):
            counters["cacheable"] += 1
            return f"cacheable:{counters['cacheable']}"

    class StatefulTool:
        definition = {
            "type": "function",
            "function": {"name": "recall", "description": "", "parameters": {"type": "object", "properties": {}}},
        }
        @staticmethod
        def execute(args):
            counters["stateful"] += 1
            return f"stateful:{counters['stateful']}"

    def call(tool_name):
        return {"id": f"call-{tool_name}", "function": {"name": tool_name, "arguments": "{}"}}

    r1 = ToolRegistry()
    r1.add_tools(CacheableTool, StatefulTool)
    messages = []
    r1._execute_one(call("read_file"), messages)
    r1._execute_one(call("read_file"), messages)
    assert counters["cacheable"] == 1
    assert messages[-1]["content"] == "cacheable:1"

    r2 = ToolRegistry()
    r2.add_tools(CacheableTool)
    r2._execute_one(call("read_file"), [])
    assert counters["cacheable"] == 2

    r1._execute_one(call("recall"), [])
    r1._execute_one(call("recall"), [])
    assert counters["stateful"] == 2


def test_normalize_tool_definition_rejects_non_object_parameters():
    with pytest.raises(ValueError):
        normalize_tool_definition({
            "type": "function",
            "function": {"name": "bad", "parameters": {"type": "array"}},
        })


def test_web_display_preserves_terminal_event_when_queue_full():
    import asyncio
    loop = asyncio.new_event_loop()
    queue = asyncio.Queue(maxsize=1)
    display = WebDisplay(queue, loop, session_id="sid")
    queue.put_nowait({"event": "text", "data": {"content": "old"}})
    display._put_with_priority({"event": "complete", "data": {"session_id": "sid"}})
    events = []
    while not queue.empty():
        events.append(queue.get_nowait()["event"])
    assert "complete" in events
    loop.close()


def test_history_persister_uses_structured_tool_call_payload(tmp_path):
    db = HistoryDB(tmp_path / "history.db", async_write=False)
    persister = HistoryPersister(db, "ws", "sid")

    assistant = {
        "role": "assistant",
        "content": "I will call a tool",
        "thinking": "hidden reasoning",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"},
        }],
    }
    tool = {"role": "tool", "tool_call_id": "call-1", "name": "read_file", "content": "file content"}

    persister(assistant)
    persister(tool)
    persister.flush_deferred([assistant, tool])

    loaded = db.load_session("ws", "sid")
    assert loaded[0]["role"] == "tool"
    assert loaded[0]["name"] == "read_file"
    assert loaded[1]["role"] == "assistant"
    assert loaded[1]["thinking"] == "hidden reasoning"
    assert loaded[1]["tool_calls"][0]["_result"] == "file content"
    db.close()


def test_history_db_batch_normalizes_message_metadata(tmp_path):
    db = HistoryDB(tmp_path / "history.db", async_write=False)
    count = db.append_batch("ws", "sid", [{
        "role": "assistant",
        "content": "plan text",
        "kind": "plan_discussion",
        "plan": {"plan_id": "p1"},
        "tool_calls": [{
            "id": "call-2",
            "function": {"name": "config", "arguments": "{}"},
            "_result": "ok",
        }],
    }])

    assert count == 1
    row = db._conn.execute("SELECT role, content, metadata FROM messages").fetchone()
    metadata = json.loads(row[2])
    assert row[:2] == ("assistant", "plan text")
    assert metadata["kind"] == "plan_discussion"
    assert metadata["plan"] == {"plan_id": "p1"}
    assert metadata["tool_calls"][0]["_result"] == "ok"

    loaded = db.load_session("ws", "sid")
    assert loaded[0]["kind"] == "plan_discussion"
    assert loaded[0]["tool_calls"][0]["function"]["name"] == "config"
    db.close()


def test_team_state_boundaries_use_structured_models(tmp_path):
    from mini_ai.team.blackboard import Blackboard
    from mini_ai.team.bus import MessageBus
    from mini_ai.team.task_graph import TaskGraph, TaskNode, TaskStatus

    bb_path = tmp_path / "blackboard.json"
    bb = Blackboard(bb_path)
    bb.put("dep", "value", author="researcher")
    detailed = bb.snapshot(detailed=True)
    assert detailed["dep"]["author"] == "researcher"
    assert json.loads(bb_path.read_text())["dep"]["value"] == "value"
    assert Blackboard(bb_path).get("dep") == "value"

    bus = MessageBus(tmp_path / "inbox")
    assert bus.send("lead", "worker", "hello") == "已送达 worker 的 inbox"
    inbox = bus.read_inbox("worker")
    assert inbox == [{
        "type": "message",
        "from": "lead",
        "content": "hello",
        "timestamp": inbox[0]["timestamp"],
    }]

    graph = TaskGraph(bb)
    task = TaskNode(id="task", agent="subagent:tester", prompt="x" * 120, depends_on=["dep"])
    graph.add_task(task)
    assert task.workflow_info().to_dict() == {
        "id": "task",
        "agent": "subagent:tester",
        "prompt": "x" * 100 + "...",
        "depends_on": ["dep"],
    }
    graph.mark_running("task")
    graph.mark_done("task", "r" * 220)
    end = graph.nodes["task"].workflow_end_event().to_dict()
    assert end["status"] == TaskStatus.DONE.value
    assert end["result_preview"] == "r" * 200 + "..."


def test_core_modules_do_not_import_web_display():
    root = Path(__file__).resolve().parents[1] / "src" / "mini_ai"
    forbidden_roots = [root / "core", root / "runner", root / "team", root / "tools"]
    offenders = []
    for base in forbidden_roots:
        for path in base.rglob("*.py"):
            if path == root / "tools" / "__init__.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "web.display" in text or "WebDisplay" in text:
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_llm_providers_reject_global_tools_true():
    from mini_ai.llm import openai, anthropic

    with pytest.raises(ValueError, match="explicit tool definitions"):
        openai._attach_tools({}, True)
    with pytest.raises(ValueError, match="explicit tool definitions"):
        anthropic.chat([{"role": "user", "content": "hi"}], tools=True, ctx=RequestContext({"model": "claude-test"}))


def test_llm_router_and_providers_default_to_no_tools():
    from mini_ai.llm import anthropic, openai, router

    assert inspect.signature(router.chat).parameters["tools"].default is None
    assert inspect.signature(router.chat_stream).parameters["tools"].default is None
    assert inspect.signature(openai.chat).parameters["tools"].default is None
    assert inspect.signature(openai.chat_stream).parameters["tools"].default is None
    assert inspect.signature(anthropic.chat).parameters["tools"].default is None
    assert inspect.signature(anthropic.chat_stream).parameters["tools"].default is None


def test_provider_message_conversion_strips_internal_fields_and_orphans():
    from mini_ai.core.messages import to_provider_messages

    messages = [
        {"role": "system", "content": "sys", "thinking": "hidden", "_pruned": True},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
            "_result": "cached result",
        }]},
        {"role": "tool", "tool_call_id": "call-1", "name": "read_file", "content": "ok"},
        {"role": "tool", "tool_call_id": "orphan", "name": "read_file", "content": "drop me"},
    ]

    converted = to_provider_messages(messages)
    assert converted[0] == {"role": "system", "content": "sys"}
    assert converted[1]["tool_calls"][0] == {
        "id": "call-1",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{}"},
    }
    assert [m.get("tool_call_id") for m in converted if m.get("role") == "tool"] == ["call-1"]


def test_config_tool_lists_session_bound_registry_tools_only():
    class LocalTool:
        definition = {
            "type": "function",
            "function": {"name": "local_only", "description": "", "parameters": {"type": "object", "properties": {}}},
        }
        @staticmethod
        def execute(args):
            return "ok"

    registry = ToolRegistry()
    registry.add_tools(LocalTool, config_tool)
    output = registry.dispatch("config", {"action": "list"})
    assert "local_only" in output
    assert "read_file" not in output


def test_runtime_sources_do_not_call_module_level_tool_registry_apis():
    root = Path(__file__).resolve().parents[1] / "src" / "mini_ai"
    allowed = {root / "tools" / "__init__.py", root / "tools" / "register_subagent.py"}
    forbidden_patterns = (
        "from ..tools import get_definitions",
        "from ...tools import get_definitions",
        "from ..tools import dispatch",
        "from ...tools import dispatch",
        "from ..tools import render_todos",
        "from ...tools import render_todos",
        "from . import render_todos",
    )
    offenders = []
    for path in root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [pattern for pattern in forbidden_patterns if pattern in text]
        if hits:
            offenders.append({"path": str(path.relative_to(root)), "hits": hits})
    assert offenders == []


def test_module_level_tool_registry_apis_fail_fast():
    from mini_ai import tools

    with pytest.raises(RuntimeError, match="session-local ToolRegistry"):
        tools.get_definitions()
    with pytest.raises(RuntimeError, match="session-local ToolRegistry"):
        tools.dispatch("read_file", {})
    with pytest.raises(RuntimeError, match="session-local ToolRegistry"):
        tools.handle_tool_calls({"tool_calls": []}, [])


def test_update_todos_display_event_does_not_depend_on_sentinel():
    from mini_ai.tools import update_todos

    class FakeDisplay:
        def __init__(self):
            self.todos = []
            self.tool_results = []

        def todos_updated(self, content):
            self.todos.append(content)

        def tool_call_start(self, name, args_summary, tool_call_id=""):
            pass

        def tool_result(self, name, result, elapsed=None, tool_call_id=""):
            self.tool_results.append((name, result))

    registry = ToolRegistry()
    registry.add_tools(update_todos)
    display = FakeDisplay()
    messages = []
    registry._execute_one({
        "id": "call-todos",
        "function": {
            "name": "update_todos",
            "arguments": json.dumps({"todos": [{"id": 1, "content": "实现架构边界", "status": "in_progress"}]}, ensure_ascii=False),
        },
    }, messages, display=display)

    assert display.todos == ["[~] **1. 实现架构边界** ← 当前"]
    assert display.tool_results == [("update_todos", "[~] **1. 实现架构边界** ← 当前")]
    assert "📋TODO" not in messages[-1]["content"]


def test_workflow_display_events_use_structured_payload_helpers():
    from mini_ai.core import events
    from mini_ai.team.models import WorkflowTaskInfo
    from mini_ai.team.task_graph import TaskStatus

    start = events.workflow_start([WorkflowTaskInfo(id="t1", agent="reviewer", prompt="检查", depends_on=["dep"])], 1).to_wire()
    assert start == {
        "event": "workflow_start",
        "data": {"tasks": [{"id": "t1", "agent": "reviewer", "prompt": "检查", "depends_on": ["dep"]}], "total": 1},
    }

    end = events.workflow_task_end("t1", TaskStatus.SKIPPED.value, error="条件不满足").to_wire()
    assert end == {"event": "task_end", "data": {"id": "t1", "status": "skipped", "error": "条件不满足"}}


def test_runtime_context_uses_explicit_protocol_boundaries():
    import typing
    from mini_ai.core.display_protocol import DisplayProtocol
    from mini_ai.core.runtime_context import SessionRuntimeContext, ToolContext
    from mini_ai.core.runtime_types import RequestContextProtocol, ToolRegistryProtocol

    tool_hints = typing.get_type_hints(ToolContext)
    runtime_hints = typing.get_type_hints(SessionRuntimeContext)

    assert tool_hints["display"] == DisplayProtocol | None
    assert runtime_hints["request_context"] is RequestContextProtocol
    assert runtime_hints["tool_registry"] is ToolRegistryProtocol
    assert "messages" in runtime_hints


def test_session_runtime_context_close_uses_owned_request_context_protocol():
    from mini_ai.core.runtime_context import SessionIdentity, SessionRuntimeContext, ToolContext

    class FakeRequestContext:
        model_config = {}
        display = None
        closed = False

        def close(self):
            self.closed = True

    class FakeRegistry:
        def get_definitions(self):
            return []

        def handle_tool_calls(self, msg, messages, display=None, persist_fn=None):
            return False

        def dispatch(self, name, args):
            return None

    identity = SessionIdentity(username="u", workspace="w", session_id="s")
    request_context = FakeRequestContext()
    runtime = SessionRuntimeContext(
        identity=identity,
        request_context=request_context,
        tool_registry=FakeRegistry(),
        tool_context=ToolContext(identity=identity),
        messages=[],
    )

    runtime.close()

    assert request_context.closed is True


def test_web_session_manager_uses_structured_component_boundaries():
    import typing
    from mini_ai.core.runtime_types import SessionComponents, TeamComponents
    from mini_ai.web.session_manager import SessionManager, SessionState, get_or_create_components

    state_hints = typing.get_type_hints(SessionState)
    manager_hints = typing.get_type_hints(SessionManager.get_components)
    team_hints = typing.get_type_hints(SessionManager.get_team_component)
    factory_hints = typing.get_type_hints(get_or_create_components)

    assert state_hints["components"] is SessionComponents
    assert manager_hints["return"] == SessionComponents | None
    assert team_hints["return"] == TeamComponents | None
    assert factory_hints["return"] is SessionComponents

    sm = SessionManager()
    sm.set_team_component("u:w", {"bus": object(), "team_mgr": object(), "blackboard": object()})
    assert set(sm.get_team_component("u:w")) == {"bus", "team_mgr", "blackboard"}


def test_message_runtime_boundaries_use_shared_aliases():
    import typing
    from mini_ai.core.chat_session import ChatSession
    from mini_ai.core.persister import HistoryPersister
    from mini_ai.core.runtime_types import HistoryDBProtocol, MessageDict, ToolDefinition
    from mini_ai.llm import base as llm_base
    from mini_ai.runner.state import LoopState

    loop_hints = typing.get_type_hints(LoopState)
    strip_hints = typing.get_type_hints(llm_base._strip_internal_fields)
    rebuild_hints = typing.get_type_hints(llm_base.rebuild_tool_messages)
    estimate_hints = typing.get_type_hints(llm_base.estimate_messages_tokens)
    chat_init_hints = typing.get_type_hints(ChatSession.__init__)
    chat_run_hints = typing.get_type_hints(ChatSession.run)
    persister_init_hints = typing.get_type_hints(HistoryPersister.__init__)
    persister_flush_hints = typing.get_type_hints(HistoryPersister.flush_deferred)

    assert loop_hints["messages"] == list[MessageDict]
    assert strip_hints == {"messages": list[MessageDict], "return": list[MessageDict]}
    assert rebuild_hints == {"messages": list[MessageDict], "return": list[MessageDict]}
    assert estimate_hints == {"messages": list[MessageDict], "return": int}
    assert chat_init_hints["messages"] == list[MessageDict]
    assert chat_run_hints["tools"] == list[ToolDefinition]
    assert chat_run_hints["return"] == MessageDict | None
    assert persister_init_hints["history_db"] is HistoryDBProtocol
    assert persister_flush_hints["messages"] == list[MessageDict]


def test_provider_adapters_use_explicit_wire_aliases():
    import typing
    from mini_ai.core.runtime_types import MessageDict, RequestContextProtocol
    from mini_ai.llm import anthropic, openai
    from mini_ai.llm.provider_types import AnthropicContentBlock, ProviderPayload, ProviderToolDefinition

    openai_provider_tools = typing.get_type_hints(openai._provider_tools)
    openai_attach_tools = typing.get_type_hints(openai._attach_tools)
    openai_chat = typing.get_type_hints(openai.chat)
    openai_stream = typing.get_type_hints(openai.chat_stream)
    anthropic_messages = typing.get_type_hints(anthropic._openai_to_anthropic)
    anthropic_tools = typing.get_type_hints(anthropic._tools_openai_to_anthropic)
    anthropic_msg = typing.get_type_hints(anthropic._anthropic_to_openai_msg)
    anthropic_chat = typing.get_type_hints(anthropic.chat)

    assert openai_provider_tools == {"tools": list[ProviderToolDefinition], "return": list[ProviderToolDefinition]}
    assert openai_attach_tools["payload"] == ProviderPayload
    assert openai_attach_tools["tools"] == list[ProviderToolDefinition] | bool | None
    assert openai_chat["messages"] == list[MessageDict]
    assert openai_chat["ctx"] == RequestContextProtocol | None
    assert openai_chat["return"] == MessageDict
    assert openai_stream["messages"] == list[MessageDict]
    assert anthropic_messages["messages"] == list[MessageDict]
    assert anthropic_tools == {"tools": list[ProviderToolDefinition], "return": list[ProviderToolDefinition]}
    assert anthropic_msg == {"ant_content": list[AnthropicContentBlock], "stop_reason": str, "return": MessageDict}
    assert anthropic_chat["messages"] == list[MessageDict]


def test_runner_error_handler_uses_structured_message_boundary():
    import typing
    from mini_ai.core.runtime_types import MessageDict
    from mini_ai.runner.error_handler import ErrorCategory, ErrorHandler, ErrorMessage

    handle_hints = typing.get_type_hints(ErrorHandler.handle)
    mini_hints = typing.get_type_hints(ErrorHandler._handle_mini_ai_error)
    unknown_hints = typing.get_type_hints(ErrorHandler._handle_unknown_error)
    dto_hints = typing.get_type_hints(ErrorMessage.to_message)

    assert handle_hints["return"] == MessageDict | None
    assert mini_hints["return"] == MessageDict | None
    assert unknown_hints["return"] == MessageDict | None
    assert dto_hints["return"] == MessageDict
    assert [category.value for category in ErrorCategory]


def test_core_message_and_event_boundaries_use_shared_aliases():
    import typing
    from mini_ai.core import events, messages
    from mini_ai.core.display_protocol import DisplayProtocol
    from mini_ai.core.runtime_types import DisplayEventPayload, DisplayWireEvent, MessageDict

    chat_from_dict = typing.get_type_hints(messages.ChatMessage.from_dict)
    chat_to_dict = typing.get_type_hints(messages.ChatMessage.to_dict)
    normalize_hints = typing.get_type_hints(messages.normalize_messages)
    provider_hints = typing.get_type_hints(messages.to_provider_messages)
    event_hints = typing.get_type_hints(events.DisplayEvent)
    wire_hints = typing.get_type_hints(events.DisplayEvent.to_wire)
    workflow_hints = typing.get_type_hints(events.workflow_start)
    display_emit = typing.get_type_hints(DisplayProtocol.emit)
    display_workflow = typing.get_type_hints(DisplayProtocol.workflow_start)

    assert chat_from_dict["data"] == MessageDict
    assert chat_to_dict["return"] == MessageDict
    assert normalize_hints["messages"] == list[MessageDict]
    assert provider_hints == {"messages": list[MessageDict], "return": list[MessageDict]}
    assert event_hints["data"] == DisplayEventPayload
    assert wire_hints["return"] == DisplayWireEvent
    assert workflow_hints["tasks"] == list[events.WirePayload | DisplayEventPayload]
    assert display_emit["data"] == DisplayEventPayload | None
    assert display_workflow["tasks"] == list[DisplayEventPayload]


def test_tool_models_use_explicit_wire_aliases():
    import typing
    from mini_ai.core.runtime_types import MessageDict, ToolFunctionPayload, ToolWirePayload
    from mini_ai.core.tool_models import ToolCall, ToolFunctionCall, ToolResult

    function_from_dict = typing.get_type_hints(ToolFunctionCall.from_dict)
    function_to_dict = typing.get_type_hints(ToolFunctionCall.to_dict)
    call_from_dict = typing.get_type_hints(ToolCall.from_dict)
    call_to_dict = typing.get_type_hints(ToolCall.to_dict)
    result_from_message = typing.get_type_hints(ToolResult.from_message)
    result_to_message = typing.get_type_hints(ToolResult.to_message)

    assert function_from_dict["data"] == ToolFunctionPayload
    assert function_to_dict["return"] == ToolFunctionPayload
    assert call_from_dict["data"] == ToolWirePayload
    assert call_to_dict["return"] == ToolWirePayload
    assert result_from_message["message"] == MessageDict
    assert result_to_message["return"] == MessageDict

    tool_call = ToolCall.from_dict({"id": "c1", "function": {"name": "read_file", "arguments": "{}"}, "_result": "ok"})
    assert tool_call.to_dict(include_result=False) == {
        "id": "c1",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{}"},
    }
    assert ToolResult("c1", "read_file", "ok").to_message(timestamp="t") == {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "read_file",
        "content": "ok",
        "timestamp": "t",
    }


def test_tool_registry_execution_uses_explicit_aliases():
    import typing
    from mini_ai.core.runtime_types import MessageDict, ToolArgs, ToolDefinition, ToolWirePayload
    from mini_ai.core.tool_models import ToolCall
    from mini_ai.tools import ToolRegistry, dispatch, get_definitions, handle_tool_calls, inject_todos

    registry_get_defs = typing.get_type_hints(ToolRegistry.get_definitions)
    registry_dispatch = typing.get_type_hints(ToolRegistry.dispatch)
    registry_handle = typing.get_type_hints(ToolRegistry.handle_tool_calls)
    registry_as_call = typing.get_type_hints(ToolRegistry._as_tool_call)
    registry_tool_message = typing.get_type_hints(ToolRegistry._tool_message)
    registry_execute_one = typing.get_type_hints(ToolRegistry._execute_one)
    registry_execute_parallel = typing.get_type_hints(ToolRegistry._execute_parallel)
    module_get_defs = typing.get_type_hints(get_definitions)
    module_dispatch = typing.get_type_hints(dispatch)
    module_handle = typing.get_type_hints(handle_tool_calls)
    module_inject = typing.get_type_hints(inject_todos)

    assert registry_get_defs["return"] == list[ToolDefinition]
    assert registry_dispatch["args"] == ToolArgs
    assert registry_handle["msg"] == MessageDict
    assert registry_handle["messages"] == list[MessageDict]
    assert registry_as_call["tc"] == ToolCall | ToolWirePayload
    assert registry_as_call["return"] is ToolCall
    assert registry_tool_message["return"] == MessageDict
    assert registry_execute_one["tc"] == ToolCall | ToolWirePayload
    assert registry_execute_one["messages"] == list[MessageDict]
    assert registry_execute_parallel["calls"] == list[ToolCall | ToolWirePayload]
    assert registry_execute_parallel["messages"] == list[MessageDict]
    assert module_get_defs["return"] == list[ToolDefinition]
    assert module_dispatch["args"] == ToolArgs
    assert module_handle["msg"] == MessageDict
    assert module_handle["messages"] == list[MessageDict]
    assert module_inject["messages"] == list[MessageDict]


def test_history_persistence_uses_explicit_boundary_types():
    import typing
    from mini_ai.core.messages import ChatMessage
    from mini_ai.core.runtime_types import HistoryContent, MessageDict
    from mini_ai.memory import history_db
    from mini_ai.memory.async_db_writer import AsyncDBWriter
    from mini_ai.memory.history_db import HistoryDB, HistoryDBPool
    from mini_ai.memory.history_types import (
        HistoryAsyncStats,
        HistoryMetadata,
        HistoryPlanArtifact,
        HistoryPoolStats,
        HistoryReviewRow,
        HistoryRuntimeMessage,
        HistorySearchRow,
        HistorySessionSummary,
        HistoryStorageRow,
    )

    metadata_hints = typing.get_type_hints(history_db._metadata_to_dict)
    row_hints = typing.get_type_hints(history_db._history_row_from_message)
    message_hints = typing.get_type_hints(history_db._message_from_history_row)
    append_hints = typing.get_type_hints(HistoryDB.append)
    batch_hints = typing.get_type_hints(HistoryDB.append_batch)
    load_hints = typing.get_type_hints(HistoryDB.load_session)
    recent_hints = typing.get_type_hints(HistoryDB.load_recent)
    search_hints = typing.get_type_hints(HistoryDB.search)
    plan_hints = typing.get_type_hints(HistoryDB.save_plan)
    list_plan_hints = typing.get_type_hints(HistoryDB.list_plans)
    sessions_hints = typing.get_type_hints(HistoryDB.list_sessions)
    review_hints = typing.get_type_hints(HistoryDB.list_for_review)
    async_batch_hints = typing.get_type_hints(AsyncDBWriter.submit_batch)
    async_cache_hints = typing.get_type_hints(AsyncDBWriter.load_session_with_cache)
    pool_hints = typing.get_type_hints(HistoryDBPool.stats)

    assert metadata_hints["return"] == HistoryMetadata
    assert row_hints["message"] == ChatMessage | HistoryRuntimeMessage | HistoryStorageRow
    assert row_hints["return"] == HistoryStorageRow
    assert message_hints["return"] == HistoryRuntimeMessage
    assert append_hints["content"] == HistoryContent
    assert batch_hints["messages"] == list[ChatMessage | HistoryRuntimeMessage]
    assert load_hints["return"] == list[HistoryRuntimeMessage]
    assert recent_hints["return"] == list[HistoryRuntimeMessage]
    assert search_hints["return"] == list[HistorySearchRow]
    assert plan_hints["artifact"] == HistoryPlanArtifact
    assert list_plan_hints["return"] == list[HistoryPlanArtifact]
    assert sessions_hints["return"] == list[HistorySessionSummary]
    assert review_hints["return"] == list[HistoryReviewRow]
    assert async_batch_hints["messages"] == list[HistoryStorageRow]
    assert async_cache_hints["return"] == list[HistoryRuntimeMessage]
    assert typing.get_type_hints(AsyncDBWriter.get_stats)["return"] == HistoryAsyncStats
    assert pool_hints["return"] == HistoryPoolStats
    assert HistoryRuntimeMessage == MessageDict


def test_plan_artifacts_use_explicit_serialization_aliases():
    import typing
    from mini_ai.core.runtime_types import HistoryDBProtocol, PlanArtifactDict, PlanStateValue
    from mini_ai.plan import artifact_parser, prompts
    from mini_ai.plan.schema import PlanArtifact, PlanSessionState, PlanSessionStateDict
    from mini_ai.plan.service import PlanService
    from mini_ai.plan.store import PlanStore

    artifact_to_dict = typing.get_type_hints(PlanArtifact.to_dict)
    artifact_from_dict = typing.get_type_hints(PlanArtifact.from_dict)
    session_to_dict = typing.get_type_hints(PlanSessionState.to_dict)
    store_init = typing.get_type_hints(PlanStore.__init__)
    store_current = typing.get_type_hints(PlanStore.current)
    store_list = typing.get_type_hints(PlanStore.list)
    store_mark = typing.get_type_hints(PlanStore.mark_status)
    parser_hints = typing.get_type_hints(artifact_parser.parse_plan_artifact)
    prompt_hints = typing.get_type_hints(prompts.build_plan_user_message)
    instruction_hints = typing.get_type_hints(prompts.build_execution_instruction)
    service_instruction = typing.get_type_hints(PlanService.execution_instruction)
    service_todos = typing.get_type_hints(PlanService.seed_execution_todos)
    service_fallback = typing.get_type_hints(PlanService._fallback_artifact)
    history_protocol = typing.get_type_hints(HistoryDBProtocol.save_plan)

    assert artifact_to_dict["return"] == PlanArtifactDict
    assert artifact_from_dict["data"] == PlanArtifactDict | None
    assert session_to_dict["return"] is PlanSessionStateDict
    assert store_init["history_db"] is HistoryDBProtocol
    assert store_current["return"] == PlanArtifactDict | None
    assert store_list["return"] == list[PlanArtifactDict]
    assert store_mark["status"] == PlanStateValue
    assert parser_hints["previous"] == PlanArtifactDict | None
    assert prompt_hints["current_plan"] == PlanArtifactDict | None
    assert instruction_hints["plan"] == PlanArtifact | PlanArtifactDict
    assert service_instruction["artifact"] == PlanArtifact | PlanArtifactDict
    assert service_todos["artifact"] == PlanArtifact | PlanArtifactDict
    assert service_fallback["current"] == PlanArtifactDict | None
    assert history_protocol["artifact"] == PlanArtifactDict


def test_web_display_uses_explicit_wire_aliases():
    import typing
    from mini_ai.core.runtime_types import DisplayEventPayload, DisplayWireEvent
    from mini_ai.team.models import WorkflowTaskInfo
    from mini_ai.web.display import WebDisplay

    emit_hints = typing.get_type_hints(WebDisplay.emit)
    push_hints = typing.get_type_hints(WebDisplay._push)
    enqueue_hints = typing.get_type_hints(WebDisplay._enqueue)
    priority_hints = typing.get_type_hints(WebDisplay._put_with_priority)
    workflow_hints = typing.get_type_hints(WebDisplay.workflow_start)

    assert emit_hints["data"] == DisplayEventPayload | None
    assert emit_hints["return"] is type(None)
    assert push_hints["data"] == DisplayEventPayload | None
    assert push_hints["return"] is type(None)
    assert enqueue_hints == {"item": DisplayWireEvent, "return": type(None)}
    assert priority_hints == {"item": DisplayWireEvent, "return": bool}
    assert workflow_hints["tasks"] == list[WorkflowTaskInfo | DisplayEventPayload]
    assert workflow_hints["return"] is type(None)


def test_tool_modules_use_explicit_argument_and_definition_aliases():
    import typing
    from mini_ai.core.runtime_types import ToolArgs, ToolDefinition, ToolParameterSchema
    from mini_ai.tools import ToolRegistry, _BoundTool, _run_with_context
    from mini_ai.tools.base import ToolBase
    from mini_ai.tools.cache import ToolCache
    from mini_ai.tools.metadata import normalize_tool_definition
    from mini_ai.tools import read_file, write_file, run_command, update_todos, dispatch_subagent
    from mini_ai.tools import workflow_tools, memory_tools, history_tools, blackboard_tools, team_tools, mcp_loader

    base_hints = typing.get_type_hints(ToolBase)
    bound_hints = typing.get_type_hints(_BoundTool.__init__)
    context_hints = typing.get_type_hints(_run_with_context)
    registry_dispatch = typing.get_type_hints(ToolRegistry.dispatch)
    normalize_hints = typing.get_type_hints(normalize_tool_definition)
    cache_get = typing.get_type_hints(ToolCache.get)
    mcp_tool_init = typing.get_type_hints(mcp_loader._MCPToolModule.__init__)
    mcp_tool_execute = typing.get_type_hints(mcp_loader._MCPToolModule.execute)
    mcp_call = typing.get_type_hints(mcp_loader.MCPConnection.call_tool)
    mcp_sync = typing.get_type_hints(mcp_loader.MCPLoader.sync_call)

    assert base_hints["parameters"] == ToolParameterSchema
    assert typing.get_type_hints(ToolBase.definition)["return"] == ToolDefinition
    assert typing.get_type_hints(ToolBase.execute)["args"] == ToolArgs
    assert bound_hints["definition"] == ToolDefinition
    assert context_hints["args"] == ToolArgs
    assert registry_dispatch["args"] == ToolArgs
    assert normalize_hints["definition"] == ToolDefinition
    assert normalize_hints["return"] == ToolDefinition
    assert cache_get["args"] == ToolArgs
    assert mcp_tool_init["definition"] == ToolDefinition
    assert mcp_tool_execute["args"] == ToolArgs
    assert mcp_call["args"] == ToolArgs
    assert mcp_sync["args"] == ToolArgs

    for module in (read_file, write_file, run_command, update_todos, dispatch_subagent):
        assert typing.get_type_hints(module.execute)["args"] == ToolArgs
        assert isinstance(module.definition, dict)
    for fn in (
        workflow_tools._run_exec,
        workflow_tools._status_exec,
        workflow_tools._load_exec,
        memory_tools._remember_exec,
        memory_tools._recall_exec,
        memory_tools._forget_exec,
        history_tools._search_exec,
        history_tools._manage_exec,
        blackboard_tools._write_exec,
        blackboard_tools._read_exec,
        blackboard_tools._list_exec,
        team_tools._spawn,
        team_tools._list,
        team_tools._send,
        team_tools._read,
        team_tools._broadcast,
        team_tools._dismiss,
    ):
        assert typing.get_type_hints(fn)["args"] == ToolArgs
