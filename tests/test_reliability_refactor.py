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
