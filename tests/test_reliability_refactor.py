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
    import typing
    from mini_ai.core.runtime_types import BlackboardDetailedSnapshot, BlackboardEntryDict, BlackboardProtocol, BlackboardTextSnapshot, InboxMessageDict, InboxMessageTypeValue, MessageBusProtocol, TeamMemberStatus, TeamMemberSummary, WorkflowTaskEndDict, WorkflowTaskInfoDict, WorkflowTaskInput, WorkflowTaskStartDict
    from mini_ai.team.blackboard import Blackboard
    from mini_ai.team.bus import MessageBus
    from mini_ai.team.models import BlackboardEntry, InboxMessage, normalize_inbox_message_type, normalize_team_status, team_member_summary, WorkflowTaskEnd, WorkflowTaskInfo, WorkflowTaskStart
    from mini_ai.team.task_graph import TaskGraph, TaskNode, TaskStatus
    from mini_ai.tools import team_tools, workflow_tools

    assert typing.get_type_hints(InboxMessage.from_dict)["data"] == InboxMessageDict
    assert typing.get_type_hints(InboxMessage.to_dict)["return"] == InboxMessageDict
    assert typing.get_type_hints(normalize_inbox_message_type)["return"] == InboxMessageTypeValue
    assert typing.get_type_hints(MessageBus.send)["msg_type"] == InboxMessageTypeValue
    assert typing.get_type_hints(MessageBusProtocol.send)["msg_type"] == InboxMessageTypeValue
    assert typing.get_type_hints(BlackboardEntry.from_dict)["data"] == BlackboardEntryDict | str
    assert typing.get_type_hints(BlackboardEntry.to_dict)["return"] == BlackboardEntryDict
    assert typing.get_type_hints(Blackboard.get)["return"] == str | object
    assert typing.get_type_hints(Blackboard.snapshot)["return"] == BlackboardTextSnapshot | BlackboardDetailedSnapshot
    assert typing.get_type_hints(BlackboardProtocol.snapshot)["return"] == BlackboardTextSnapshot | BlackboardDetailedSnapshot
    assert typing.get_type_hints(TaskGraph.__init__)["blackboard"] == BlackboardProtocol
    assert typing.get_type_hints(workflow_tools.normalize_workflow_tasks)["return"] == list[WorkflowTaskInput]
    assert typing.get_type_hints(WorkflowTaskInfo.to_dict)["return"] == WorkflowTaskInfoDict
    assert typing.get_type_hints(WorkflowTaskStart.to_dict)["return"] == WorkflowTaskStartDict
    assert typing.get_type_hints(WorkflowTaskEnd.to_dict)["return"] == WorkflowTaskEndDict
    assert typing.get_type_hints(normalize_team_status)["return"] == TeamMemberStatus
    assert typing.get_type_hints(team_member_summary)["return"] == TeamMemberSummary
    assert normalize_team_status("invalid") == "offline"
    assert normalize_inbox_message_type("invalid") == "message"
    assert team_member_summary("a", "r", "working") == {"name": "a", "role": "r", "status": "working"}
    assert workflow_tools.normalize_workflow_tasks([
        {"id": "task", "agent": "subagent:tester", "prompt": "do it", "depends_on": ["dep", 1], "timeout": "30"},
        {"id": "", "agent": "missing", "prompt": "skip"},
        "bad",
    ]) == [{
        "id": "task",
        "agent": "subagent:tester",
        "prompt": "do it",
        "depends_on": ["dep", "1"],
        "max_retry": 1,
        "timeout": 30,
    }]

    bb_path = tmp_path / "blackboard.json"
    bb = Blackboard(bb_path)
    bb.put("dep", "value", author="researcher")
    detailed = bb.snapshot(detailed=True)
    text_snapshot = bb.snapshot()
    assert detailed["dep"]["author"] == "researcher"
    assert text_snapshot == {"dep": "value"}
    assert json.loads(bb_path.read_text())["dep"]["value"] == "value"
    assert Blackboard(bb_path).get("dep") == "value"

    bus = MessageBus(tmp_path / "inbox")
    assert bus.send("lead", "worker", "hello") == "已送达 worker 的 inbox"
    assert bus.send("lead", "worker", "bad", "invalid") == "Error: invalid msg_type 'invalid'"
    peeked = bus.read_inbox("worker", peek=True)
    inbox = bus.read_inbox("worker")
    assert peeked == inbox == [{
        "type": "message",
        "from": "lead",
        "content": "hello",
        "timestamp": inbox[0]["timestamp"],
    }]
    assert bus.read_inbox("worker") == []
    assert team_tools.send_from_args(bus, "lead", {"to": "worker", "content": "normalized", "msg_type": "invalid"}) == "已送达 worker 的 inbox"
    normalized = bus.read_inbox("worker")
    assert normalized[0]["type"] == "message"
    assert normalized[0]["content"] == "normalized"

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
    import typing
    from mini_ai.core.runtime_types import MessageDict
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
    assert update_todos.TodoStatus
    assert typing.get_type_hints(update_todos.get_todos)["return"] == list[update_todos.TodoItem]
    assert typing.get_type_hints(update_todos.set_todos)["todos"] == list[update_todos.TodoInput]
    assert typing.get_type_hints(update_todos.inject_todos)["messages"] == list[MessageDict]


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


def test_runtime_protocols_use_structured_team_and_subagent_aliases():
    import typing
    from mini_ai.core.runtime_types import (
        InboxMessageDict,
        MessageBusProtocol,
        MetadataDict,
        SubagentCreateInput,
        SubagentListText,
        SubagentLoaderProtocol,
        SubagentSpec,
        TeamConfigDict,
        TeamListText,
        TeamManagerProtocol,
        TeamMemberSummary,
        TeamStatusResponse,
    )
    from mini_ai.subagents import SubagentLoader
    from mini_ai.team.bus import MessageBus
    from mini_ai.team.manager import TeammateManager
    from mini_ai.team.models import InboxMessage, TeamStatusResponse as TeamStatusResponseExport
    from mini_ai.web.routes import team as team_routes

    protocol_sub_list = typing.get_type_hints(SubagentLoaderProtocol.list_specs)
    protocol_sub_get = typing.get_type_hints(SubagentLoaderProtocol.get)
    protocol_sub_has = typing.get_type_hints(SubagentLoaderProtocol.has)
    protocol_sub_create = typing.get_type_hints(SubagentLoaderProtocol.create)
    protocol_inbox = typing.get_type_hints(MessageBusProtocol.read_inbox)
    protocol_team = typing.get_type_hints(TeamManagerProtocol)
    protocol_team_list = typing.get_type_hints(TeamManagerProtocol.list_all)
    protocol_member_summaries = typing.get_type_hints(TeamManagerProtocol.member_summaries)
    protocol_member_names = typing.get_type_hints(TeamManagerProtocol.member_names)
    protocol_active_member_names = typing.get_type_hints(TeamManagerProtocol.active_member_names)
    protocol_has_working_members = typing.get_type_hints(TeamManagerProtocol.has_working_members)
    loader_hints = typing.get_type_hints(SubagentLoader)
    loader_parse = typing.get_type_hints(SubagentLoader._parse_frontmatter)
    loader_list = typing.get_type_hints(SubagentLoader.list_specs)
    loader_get = typing.get_type_hints(SubagentLoader.get)
    loader_has = typing.get_type_hints(SubagentLoader.has)
    loader_create = typing.get_type_hints(SubagentLoader.create)
    bus_read = typing.get_type_hints(MessageBus.read_inbox)
    inbox_to_dict = typing.get_type_hints(InboxMessage.to_dict)
    manager_load = typing.get_type_hints(TeammateManager._load_config)
    manager_find = typing.get_type_hints(TeammateManager._find)
    manager_list = typing.get_type_hints(TeammateManager.list_all)
    manager_member_summaries = typing.get_type_hints(TeammateManager.member_summaries)
    route_status = typing.get_type_hints(team_routes.team_status)

    assert protocol_sub_list["return"] is SubagentListText
    assert protocol_sub_get["return"] == SubagentSpec | None
    assert protocol_sub_has["return"] is bool
    assert protocol_sub_create["data"] is SubagentCreateInput
    assert protocol_sub_create["return"] == Path | None
    assert protocol_inbox["return"] == list[InboxMessageDict]
    assert protocol_inbox["peek"] is bool
    assert "config" not in protocol_team
    assert "lock" not in protocol_team
    assert protocol_team_list["return"] is TeamListText
    assert protocol_member_summaries["return"] == list[TeamMemberSummary]
    assert protocol_member_names["return"] == list[str]
    assert protocol_active_member_names["return"] == list[str]
    assert protocol_has_working_members["return"] is bool
    assert typing.get_type_hints(SubagentCreateInput) == {
        "name": str,
        "description": str,
        "prompt": str,
        "tools": list[str],
        "max_turns": int,
    }
    assert loader_hints["specs"] == dict[str, SubagentSpec]
    assert loader_parse["return"] == tuple[MetadataDict, str]
    assert loader_list["return"] is SubagentListText
    assert loader_get["return"] == SubagentSpec | None
    assert loader_has["return"] is bool
    assert loader_create["data"] is SubagentCreateInput
    assert loader_create["return"] == Path | None
    assert bus_read["return"] == list[InboxMessageDict]
    assert inbox_to_dict["return"] is InboxMessageDict
    assert manager_load["return"] is TeamConfigDict
    assert manager_find["return"] == TeamMemberSummary | None
    assert manager_list["return"] is TeamListText
    assert manager_member_summaries["return"] == list[TeamMemberSummary]
    assert route_status["return"] is TeamStatusResponse
    assert TeamStatusResponseExport is TeamStatusResponse


def test_tool_modules_use_explicit_argument_and_definition_aliases():
    import typing
    from mini_ai.core.runtime_types import ToolArgs, ToolDefinition, ToolParameterSchema
    from mini_ai.tools import ToolRegistry, _BoundTool
    from mini_ai.tools.base import ToolBase
    from mini_ai.tools.cache import ToolCache
    from mini_ai.tools.metadata import normalize_tool_definition
    from mini_ai.tools import read_file, write_file, run_command, update_todos, dispatch_subagent
    from mini_ai.tools import workflow_tools, memory_tools, history_tools, blackboard_tools, team_tools, mcp_loader

    base_hints = typing.get_type_hints(ToolBase)
    bound_hints = typing.get_type_hints(_BoundTool.__init__)
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


def test_provider_usage_uses_explicit_usage_aliases():
    import typing
    from mini_ai.core.application_service import RunTurnResult
    from mini_ai.core.runtime_types import UsageDict
    from mini_ai.llm import base
    from mini_ai.llm.provider_types import ProviderUsage
    from mini_ai.runner.executor import ToolExecutor
    from mini_ai.web.session_manager import SessionManager, SessionState

    assert typing.get_type_hints(base)["_global_usage"] == UsageDict
    assert typing.get_type_hints(base.get_usage)["return"] == UsageDict
    assert typing.get_type_hints(base.get_global_usage)["return"] == UsageDict
    assert typing.get_type_hints(RunTurnResult)["usage"] == UsageDict
    assert typing.get_type_hints(SessionState)["last_usage"] == UsageDict
    assert typing.get_type_hints(SessionManager.set_last_usage)["usage"] == UsageDict
    assert typing.get_type_hints(ToolExecutor._call_llm_stream)
    assert ProviderUsage == UsageDict


def test_settings_and_config_boundaries_use_explicit_aliases():
    import typing
    import mini_ai.config as config
    from mini_ai.core import settings as settings_mod
    from mini_ai.core import runtime_types
    from mini_ai.core.display_protocol import DisplayProtocol
    from mini_ai.core.runtime_factory import build_session_runtime
    from mini_ai.core.runtime_types import (
        ConfigDict,
        DatabaseConfigDict,
        DatabaseHistoryConfigDict,
        DisplayConfigDict,
        ModelConfigDict,
        RawConfigDict,
        RequestContextProtocol,
        RunnerConfigDict,
        TimeoutConfigDict,
        ToolConfigDict,
    )

    config_hints = typing.get_type_hints(config)
    assert config_hints["_raw"] == RawConfigDict
    assert config_hints["MODEL_CONFIG"] == ModelConfigDict
    assert config_hints["TIMEOUTS"] == TimeoutConfigDict
    assert config_hints["RUNNER"] == RunnerConfigDict
    assert config_hints["DISPLAY"] == DisplayConfigDict
    assert config_hints["TOOL"] == ToolConfigDict
    assert config_hints["DATABASE"] == DatabaseConfigDict

    assert typing.get_type_hints(settings_mod.ModelSettings)["headers"] == ConfigDict
    assert typing.get_type_hints(settings_mod.ModelSettings)["thinking"] == ConfigDict
    assert typing.get_type_hints(settings_mod.ModelSettings)["extra"] == ConfigDict
    assert typing.get_type_hints(settings_mod.ModelSettings.from_dict)["data"] == ModelConfigDict | None
    assert typing.get_type_hints(settings_mod.ModelSettings.to_dict)["return"] == ModelConfigDict
    assert typing.get_type_hints(settings_mod.TimeoutSettings.from_dict)["data"] == TimeoutConfigDict | None
    assert typing.get_type_hints(settings_mod.TimeoutSettings.to_dict)["return"] == TimeoutConfigDict
    assert typing.get_type_hints(settings_mod.RunnerSettings.from_dict)["data"] == RunnerConfigDict | None
    assert typing.get_type_hints(settings_mod.RunnerSettings.to_dict)["return"] == RunnerConfigDict
    assert typing.get_type_hints(settings_mod.DisplaySettings.from_dict)["data"] == DisplayConfigDict | None
    assert typing.get_type_hints(settings_mod.DisplaySettings.to_dict)["return"] == DisplayConfigDict
    assert typing.get_type_hints(settings_mod.ToolSettings.from_dict)["data"] == ToolConfigDict | None
    assert typing.get_type_hints(settings_mod.ToolSettings.to_dict)["return"] == ToolConfigDict
    assert typing.get_type_hints(settings_mod.DatabaseHistorySettings.from_dict)["data"] == DatabaseHistoryConfigDict | None
    assert typing.get_type_hints(settings_mod.DatabaseHistorySettings.to_dict)["return"] == DatabaseHistoryConfigDict
    assert typing.get_type_hints(settings_mod.DatabaseSettings.from_dict)["data"] == DatabaseConfigDict | None
    assert typing.get_type_hints(settings_mod.DatabaseSettings.to_dict)["return"] == DatabaseConfigDict

    snapshot_hints = typing.get_type_hints(settings_mod.SettingsSnapshot.from_config_dicts)
    assert snapshot_hints["model_config"] == ModelConfigDict | None
    assert snapshot_hints["timeouts"] == TimeoutConfigDict | None
    assert snapshot_hints["runner"] == RunnerConfigDict | None
    assert snapshot_hints["display"] == DisplayConfigDict | None
    assert snapshot_hints["tool"] == ToolConfigDict | None
    assert snapshot_hints["database"] == DatabaseConfigDict | None

    assert typing.get_type_hints(config.get_model_config)["return"] == ModelConfigDict | None
    assert typing.get_type_hints(config.RequestContext.__init__)["model_config"] == ModelConfigDict
    assert typing.get_type_hints(config.AppConfig.model_config.fget)["return"] == ModelConfigDict
    assert typing.get_type_hints(config.AppConfig.timeouts.fget)["return"] == TimeoutConfigDict
    assert typing.get_type_hints(config.AppConfig.runner.fget)["return"] == RunnerConfigDict
    assert typing.get_type_hints(config.AppConfig.display.fget)["return"] == DisplayConfigDict
    assert typing.get_type_hints(config.AppConfig.tool.fget)["return"] == ToolConfigDict
    assert typing.get_type_hints(config.AppConfig.database.fget)["return"] == DatabaseConfigDict
    assert typing.get_type_hints(RequestContextProtocol, globalns={**vars(runtime_types), "DisplayProtocol": DisplayProtocol})["model_config"] == ModelConfigDict
    assert typing.get_type_hints(build_session_runtime)["model_config"] == ModelConfigDict | None


def test_settings_snapshot_preserves_config_extras_and_deep_copies():
    from mini_ai.core.settings import SettingsSnapshot

    raw_model = {
        "api_url": "https://example.test",
        "api_key": "k",
        "model": "m",
        "headers": {"X-Test": "1"},
        "thinking": {"enabled": True},
        "vendor_extra": {"nested": ["a"]},
    }

    snapshot = SettingsSnapshot.from_config_dicts(
        model_config=raw_model,
        timeouts={"llm": 9, "custom_timeout": {"x": 1}},
        database={"history": {"on_full": "drop", "custom_history": 2}},
    )

    raw_model["headers"]["X-Test"] = "mutated"

    model_out = snapshot.model.to_dict()
    assert model_out["headers"] == {"X-Test": "1"}
    assert model_out["thinking"] == {"enabled": True}
    assert model_out["vendor_extra"] == {"nested": ["a"]}

    model_out["headers"]["X-Test"] = "mutated-again"
    assert snapshot.model.headers == {"X-Test": "1"}
    assert snapshot.timeouts.to_dict()["custom_timeout"] == {"x": 1}
    assert snapshot.database.to_dict()["history"]["custom_history"] == 2


def test_web_route_boundaries_use_explicit_dtos():
    import typing
    from mini_ai.core.runtime_types import DisplayWireEvent, PlanArtifactDict, TeamComponents
    from mini_ai.web import route_types, session_manager
    from mini_ai.web.routes import chat, commands, config as config_routes, files, models, sessions, skills, team, workspaces

    assert typing.get_type_hints(session_manager._build_meta)["return"] == route_types.SessionMeta
    assert typing.get_type_hints(session_manager.SessionManager.get_meta)["return"] == route_types.SessionMeta | None
    assert typing.get_type_hints(session_manager.SessionManager.set_meta)["meta"] == route_types.SessionMeta

    assert typing.get_type_hints(sessions.create_session)["body"] == route_types.SessionCreateRequest
    assert typing.get_type_hints(sessions.create_session)["return"] == route_types.SessionCreateResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(sessions.list_sessions)["return"] == route_types.SessionListResponse
    assert typing.get_type_hints(sessions.get_todos)["return"] == route_types.TodosResponse
    assert typing.get_type_hints(sessions.delete_session)["body"] == route_types.SessionDeleteRequest
    assert typing.get_type_hints(sessions.batch_delete_sessions)["return"] == route_types.SessionBatchDeleteResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(sessions.rename_session)["return"] == route_types.SessionRenameResponse | route_types.RouteErrorResponse

    assert typing.get_type_hints(chat.chat_ws_endpoint)["ws"]
    assert typing.get_type_hints(chat.chat_history)["return"] == route_types.ChatHistoryResponse
    assert typing.get_type_hints(chat.chat_reset)["body"] == route_types.ChatResetRequest | None
    assert typing.get_type_hints(chat.chat_reset)["return"] == route_types.ChatResetResponse | route_types.RouteErrorResponse

    assert typing.get_type_hints(files._list_files_sync)["return"] == route_types.FileListResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(files._search_files_sync)["return"] == route_types.FileSearchResponse
    assert typing.get_type_hints(files._browse_dirs_sync)["return"] == route_types.BrowseDirsResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(files.list_files)["return"] == route_types.FileListResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(files.read_file)["return"] == route_types.FileReadTextResponse | route_types.FileReadBinaryResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(files.search_files)["return"] == route_types.FileSearchResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(files.browse_dirs)["return"] == route_types.BrowseDirsResponse | route_types.RouteErrorResponse

    assert typing.get_type_hints(models.list_models)["return"] == route_types.ModelsResponse
    assert typing.get_type_hints(models.switch_model_endpoint)["body"] == route_types.SwitchModelRequest
    assert typing.get_type_hints(models.switch_model_endpoint)["return"] == route_types.SwitchModelResponse | route_types.RouteErrorResponse

    assert typing.get_type_hints(workspaces.list_workspaces)["return"] == route_types.WorkspaceListResponse
    assert typing.get_type_hints(workspaces.create_workspace)["body"] == route_types.WorkspaceCreateRequest
    assert typing.get_type_hints(workspaces.add_workspace)["body"] == route_types.WorkspaceAddRequest
    assert typing.get_type_hints(workspaces.switch_workspace)["return"] == route_types.WorkspaceSwitchResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(workspaces.list_removed_workspaces)["return"] == route_types.RemovedWorkspacesResponse
    assert typing.get_type_hints(workspaces.restore_workspace)["body"] == route_types.WorkspaceRestoreRequest
    assert typing.get_type_hints(workspaces.delete_removed_workspace)["return"] == route_types.WorkspaceActionResponse | route_types.RouteErrorResponse

    assert typing.get_type_hints(commands)["_WEB_COMMANDS"] == list[route_types.WebCommand]
    assert typing.get_type_hints(commands.list_commands)["return"] == route_types.CommandsResponse
    assert typing.get_type_hints(commands.mcp_status)["return"] == route_types.McpStatusResponse

    assert typing.get_type_hints(config_routes.get_config)["return"] == route_types.ConfigResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(config_routes.get_system_prompt)["return"] == route_types.SystemPromptResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(config_routes.get_tools)["return"] == route_types.ToolsResponse
    assert typing.get_type_hints(config_routes.get_settings)["return"] == route_types.SettingsResponse
    assert typing.get_type_hints(config_routes.update_settings)["body"] == route_types.SettingsUpdateRequest
    assert typing.get_type_hints(config_routes.update_settings)["return"] == route_types.SettingsUpdateResponse
    assert typing.get_type_hints(config_routes.add_model)["body"] == route_types.AddModelRequest
    assert typing.get_type_hints(config_routes.add_model)["return"] == route_types.AddModelResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(config_routes.remove_model)["body"] == route_types.RemoveModelRequest
    assert typing.get_type_hints(config_routes.remove_model)["return"] == route_types.RemoveModelResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(config_routes.add_mcp_server)["body"] == route_types.McpServerAddRequest
    assert typing.get_type_hints(config_routes.add_mcp_server)["return"] == route_types.McpServerAddResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(config_routes.remove_mcp_server)["return"] == route_types.McpServerRemoveResponse | route_types.RouteErrorResponse

    assert typing.get_type_hints(skills.list_skills)["return"] == route_types.SkillsListResponse
    assert typing.get_type_hints(skills.get_skill_info)["return"] == route_types.SkillInfoResponse
    assert typing.get_type_hints(skills.load_skill)["return"] == route_types.SkillLoadResponse
    assert typing.get_type_hints(skills.install_skill)["return"] == route_types.SkillInstallResponse | route_types.RouteErrorResponse
    assert typing.get_type_hints(skills.create_skill)["return"] == route_types.SkillCreateResponse
    assert typing.get_type_hints(skills.delete_skill)["return"] == route_types.SkillDeleteResponse | route_types.RouteErrorResponse

    assert typing.get_type_hints(team._get_team_comp)["return"] == TeamComponents | None
    assert typing.get_type_hints(team.blackboard_snapshot)["return"] == route_types.BlackboardSnapshotResponse
    assert typing.get_type_hints(team.dismiss_teammate)["body"] == route_types.DismissTeammateRequest
    assert typing.get_type_hints(team.clear_blackboard)["body"] == route_types.ClearBlackboardRequest
    assert route_types.DisplayWireEvent == DisplayWireEvent
    assert route_types.PlanArtifactDict == PlanArtifactDict
    assert route_types.TeamComponents == TeamComponents


def test_frontend_rest_boundaries_stay_in_api_module():
    repo = Path(__file__).resolve().parents[1]
    api_text = (repo / "web/src/api.ts").read_text()
    component_files = sorted((repo / "web/src/components").glob("*.vue"))

    assert "_fetchJson" not in api_text
    assert [line.strip() for line in api_text.splitlines() if "_origFetch" in line] == [
        "const _origFetch = window.fetch.bind(window)",
        "const resp = await _origFetch(url, { ...init, signal: controller.signal })",
    ]
    for wrapper in ("listFiles", "readFile", "searchFiles", "getWorkspaces", "switchWorkspace"):
        assert f"function {wrapper}" in api_text

    offenders = []
    for path in component_files:
        text = path.read_text()
        if "fetch(" in text or "resp.json()" in text:
            offenders.append(path.relative_to(repo).as_posix())
    assert offenders == []


def test_tool_registry_uses_session_local_tool_bindings():
    repo = Path(__file__).resolve().parents[1]
    registry_text = (repo / "src/mini_ai/tools/__init__.py").read_text()

    forbidden_registry_bindings = (
        "team_tools.configure",
        "team_tools.set_caller",
        "blackboard_tools.configure",
        "workflow_tools.configure",
        "list_skills.configure",
        "load_skill.configure",
        "install_skill.configure",
        "delete_skill.configure",
        "memory_tools.configure",
        "history_tools.configure",
        "config_tool.configure",
        "config_tool._registry_ctx",
        "dispatch_subagent.configure",
        "register_subagent.configure",
        "dispatch_subagent._loader",
        "registry._by_name",
        "from ..team.task_graph import TaskGraph",
    )
    for pattern in forbidden_registry_bindings:
        assert pattern not in registry_text
    assert "workflow_tools.run_workflow_with_context" in registry_text
    assert "run_command.execute_with_cwd" in registry_text
    assert "blackboard_tools.write_to_blackboard" in registry_text
    assert "team_tools.spawn_from_args" in registry_text
    assert "list_skills.list_skills_with_loader" in registry_text
    assert "load_skill.load_skill_with_loader" in registry_text
    assert "install_skill.install_skill_with_loader" in registry_text
    assert "delete_skill.delete_skill_with_loader" in registry_text
    assert "memory_tools.remember_with_store" in registry_text
    assert "history_tools.search_history_with_db" in registry_text
    assert "config_tool.execute_with_registry" in registry_text
    assert "dispatch_subagent.execute_with_context" in registry_text
    assert "register_subagent.execute_with_context" in registry_text


def test_skill_tools_do_not_keep_module_level_loader_state():
    repo = Path(__file__).resolve().parents[1]
    skill_tool_paths = [
        repo / "src/mini_ai/tools/list_skills.py",
        repo / "src/mini_ai/tools/load_skill.py",
        repo / "src/mini_ai/tools/install_skill.py",
        repo / "src/mini_ai/tools/delete_skill.py",
    ]
    forbidden = ("_loader_var", "_loader =", "def configure", "_get_loader")

    offenders = []
    for path in skill_tool_paths:
        text = path.read_text()
        hits = [pattern for pattern in forbidden if pattern in text]
        if hits:
            offenders.append({"path": path.name, "hits": hits})
    assert offenders == []


def test_loader_tools_use_public_loader_boundaries():
    repo = Path(__file__).resolve().parents[1]
    checked = {
        "tools/install_skill.py": ("._load_all(", "._tier_paths", "loader._"),
        "tools/memory_tools.py": ("store._tier_paths", "hasattr(store, '_tier_paths')"),
        "cli/commands.py": ("skill_loader._load_all",),
        "core/tool_registry_factory.py": ("registry._project_path",),
    }

    offenders = []
    for rel, forbidden in checked.items():
        text = (repo / "src/mini_ai" / rel).read_text()
        hits = [pattern for pattern in forbidden if pattern in text]
        if hits:
            offenders.append({"path": rel, "hits": hits})
    assert offenders == []


def test_run_command_uses_explicit_default_cwd(tmp_path):
    from mini_ai.tools import run_command

    output = run_command.execute_with_cwd(str(tmp_path), {"command": "pwd"})
    assert output == str(tmp_path)


def test_run_command_does_not_depend_on_dispatch_context():
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "src/mini_ai/tools/run_command.py").read_text()
    forbidden = ("dispatch_subagent", "get_project_path", "ContextVar", "contextvars")
    hits = [pattern for pattern in forbidden if pattern in text]
    assert hits == []


def test_team_tools_do_not_keep_module_level_dependencies():
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "src/mini_ai/tools/team_tools.py").read_text()
    forbidden = ("def configure", "_bus:", "_bus =", "_manager:", "_manager =", "_require_bus", "_require_manager")
    hits = [pattern for pattern in forbidden if pattern in text]
    assert hits == []
    assert "team_caller" in text
    assert "def set_caller" in text


def test_config_tool_does_not_keep_module_level_registry_state():
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "src/mini_ai/tools/config_tool.py").read_text()
    forbidden = ("import contextvars", "def configure", "_registry_ctx", "ContextVar")
    hits = [pattern for pattern in forbidden if pattern in text]
    assert hits == []


def test_memory_history_tools_do_not_keep_module_level_runtime_state():
    repo = Path(__file__).resolve().parents[1]
    modules = {
        "memory_tools.py": ("import contextvars", "def configure", "_get_store", "_memory_store"),
        "history_tools.py": ("import contextvars", "def configure", "_get_db", "_get_workspace", "_history_db", "_current_workspace"),
    }

    offenders = []
    for filename, forbidden in modules.items():
        path = repo / "src/mini_ai/tools" / filename
        text = path.read_text()
        hits = [pattern for pattern in forbidden if pattern in text]
        if hits:
            offenders.append({"path": filename, "hits": hits})
    assert offenders == []


def test_blackboard_workflow_tools_do_not_keep_module_level_runtime_state():
    repo = Path(__file__).resolve().parents[1]
    modules = {
        "blackboard_tools.py": ("def configure", "_blackboard:", "_blackboard =", "_require_blackboard"),
        "workflow_tools.py": (
            "def configure",
            "_blackboard:",
            "_blackboard =",
            "_workflow_dirs",
            "_last_graphs",
            "_graphs_lock",
            "_bus:",
            "_manager:",
            "_display =",
        ),
    }

    offenders = []
    for filename, forbidden in modules.items():
        path = repo / "src/mini_ai/tools" / filename
        text = path.read_text()
        hits = [pattern for pattern in forbidden if pattern in text]
        if hits:
            offenders.append({"path": filename, "hits": hits})
    assert offenders == []


def test_subagent_loader_create_serializes_yaml_and_reloads_cleanly(tmp_path):
    from mini_ai.subagents import SubagentLoader

    loader = SubagentLoader(tmp_path)
    created = loader.create({
        "name": "quoted-agent",
        "description": "含: 冒号 # 与中文",
        "prompt": "系统提示\n第二行",
        "tools": ["read_file", "run_command"],
        "max_turns": 3,
    })

    assert created == tmp_path / "quoted-agent.md"
    text = created.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "tools:\n- read_file\n- run_command" in text
    assert loader.get("quoted-agent") == {
        "name": "quoted-agent",
        "description": "含: 冒号 # 与中文",
        "system_prompt": "系统提示\n第二行",
        "tool_names": ["read_file", "run_command"],
        "max_turns": 3,
    }

    created.unlink()
    loader.reload()
    assert loader.specs == {}


def test_subagent_tools_do_not_keep_module_level_runtime_state():
    repo = Path(__file__).resolve().parents[1]
    modules = {
        "dispatch_subagent.py": (
            "import contextvars",
            "def configure",
            "_loader =",
            "_definition =",
            "_project_path_ctx",
            "_display_ctx",
            "_registry_ctx",
        ),
        "register_subagent.py": (
            "import contextvars",
            "def configure",
            "_loader =",
            "_subagents_dir",
            "subagents_dir",
            "loader.specs",
            "loader._load_all",
            "frontmatter",
            "safe_dump",
            "_registry_ctx",
            "registry._by_name",
            "dispatch_subagent._loader",
        ),
    }

    offenders = []
    for filename, forbidden in modules.items():
        path = repo / "src/mini_ai/tools" / filename
        text = path.read_text()
        hits = [pattern for pattern in forbidden if pattern in text]
        if hits:
            offenders.append({"path": filename, "hits": hits})
    assert offenders == []


def test_team_orchestrator_uses_display_protocol_methods_directly():
    repo = Path(__file__).resolve().parents[1]
    orchestrator_text = (repo / "src/mini_ai/team/orchestrator.py").read_text()

    assert "from ..core import events" not in orchestrator_text
    assert "_push_event" not in orchestrator_text
    assert ".emit(" not in orchestrator_text
    for method in ("workflow_start", "workflow_task_start", "workflow_task_end", "workflow_end"):
        assert f"self._display.{method}" in orchestrator_text


def test_non_adapter_modules_do_not_reach_display_transport_details():
    import ast

    repo = Path(__file__).resolve().parents[1]
    src_root = repo / "src/mini_ai"
    private_attrs = {"queue", "loop", "_push", "_thinking_buf"}
    offenders = []

    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root).as_posix()
        if rel.startswith(("web/", "cli/")):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("web.display"):
                offenders.append(f"{rel}:{node.lineno}: imports WebDisplay")
            elif isinstance(node, ast.Attribute) and node.attr in private_attrs:
                offenders.append(f"{rel}:{node.lineno}: accesses display transport attr {node.attr}")

    assert offenders == []
