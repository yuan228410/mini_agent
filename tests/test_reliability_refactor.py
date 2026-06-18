import sqlite3
import threading
import time

import pytest

from mini_ai.memory.async_db_writer import AsyncDBWriter
from mini_ai.tools import ToolRegistry
from mini_ai.tools.metadata import normalize_tool_definition
from mini_ai.web.display import WebDisplay
from mini_ai.config import RequestContext


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
