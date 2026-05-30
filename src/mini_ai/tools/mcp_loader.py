"""MCP (Model Context Protocol) 客户端加载器"""
import asyncio
import json
import threading
from typing import Any

from ..config import MCP
from ..logger import logger

_MCP_ENABLED = MCP.get("enabled", False)
_MCP_SERVERS = MCP.get("servers", {})
_CONNECT_TIMEOUT = MCP.get("connect_timeout", 10)
_EXECUTE_TIMEOUT = MCP.get("execute_timeout", 60)
_SSE_READ_TIMEOUT = MCP.get("sse_read_timeout", 120)


class MCPConnection:
    def __init__(self, server_name: str, cfg: dict):
        self.name = server_name
        self.cfg = cfg
        self.conn_type = cfg.get("type") or ("streamable_http" if cfg.get("url") else "stdio")
        self.session = None
        self.exit_stack = None
        self.tools: list[dict] = []

    async def connect(self) -> bool:
        if self.cfg.get("disabled"):
            logger.info(f"[MCP] 跳过已禁用的服务器: {self.name}")
            return False
        try:
            if self.conn_type == "stdio":
                return await self._connect_stdio()
            elif self.conn_type in ("streamable_http", "sse"):
                return await self._connect_http()
            else:
                logger.warning(f"[MCP] 未知连接类型 '{self.conn_type}': {self.name}")
                return False
        except Exception as e:
            logger.warning(f"[MCP] 连接 {self.name} 失败: {e}")
            return False

    async def _connect_stdio(self) -> bool:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = self.cfg.get("command")
        if not command:
            logger.warning(f"[MCP] stdio 服务器 {self.name} 缺少 command")
            return False

        from contextlib import AsyncExitStack
        self.exit_stack = AsyncExitStack()

        server_params = StdioServerParameters(
            command=command,
            args=self.cfg.get("args", []),
            env=self.cfg.get("env") or None,
        )
        read_stream, write_stream = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self.session.initialize()
        await self._fetch_tools()
        return True

    async def _connect_http(self) -> bool:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from contextlib import AsyncExitStack

        url = self.cfg.get("url")
        if not url:
            logger.warning(f"[MCP] {self.conn_type} 服务器 {self.name} 缺少 url")
            return False

        self.exit_stack = AsyncExitStack()

        kwargs: dict[str, Any] = {"url": url}
        headers = self.cfg.get("headers")
        if headers:
            kwargs["headers"] = headers
        kwargs["timeout"] = _CONNECT_TIMEOUT
        kwargs["sse_read_timeout"] = _SSE_READ_TIMEOUT

        read_stream, write_stream, _ = await self.exit_stack.enter_async_context(
            streamablehttp_client(**kwargs)
        )
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self.session.initialize()
        await self._fetch_tools()
        return True

    async def _fetch_tools(self):
        result = await self.session.list_tools()
        self.tools = result.tools
        logger.info(f"[MCP] {self.name}: 发现 {len(self.tools)} 个工具")

    async def call_tool(self, tool_name: str, args: dict) -> str:
        timeout = self.cfg.get("execute_timeout", _EXECUTE_TIMEOUT)
        async with asyncio.timeout(timeout):
            result = await self.session.call_tool(tool_name, arguments=args)
        parts = []
        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
            else:
                parts.append(str(item))
        content = "\n".join(parts)
        if getattr(result, "isError", False):
            content = f"[MCP 错误] {content}"
        return content

    async def disconnect(self):
        if self.exit_stack:
            try:
                await self.exit_stack.aclose()
            except Exception:
                pass
            finally:
                self.exit_stack = None
                self.session = None


class _MCPToolModule:
    __slots__ = ("definition", "_loader", "_server_name", "_tool_name", "_orig_name")

    def __init__(self, definition: dict, loader: "MCPLoader", server_name: str, tool_name: str, orig_name: str):
        self.definition = definition
        self._loader = loader
        self._server_name = server_name
        self._tool_name = tool_name
        self._orig_name = orig_name

    def execute(self, args: dict) -> str:
        return self._loader.sync_call(self._server_name, self._orig_name, args)


class MCPLoader:
    def __init__(self):
        self._connections: dict[str, MCPConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._tool_modules: list[_MCPToolModule] = []

    def start_sync(self) -> list[_MCPToolModule]:
        if not _MCP_SERVERS:
            return []
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._started.wait(timeout=30)
        if not self._loop or not self._thread.is_alive():
            logger.warning("[MCP] 后台 event loop 启动失败或线程异常退出")
            return []
        future = asyncio.run_coroutine_threadsafe(self._start_all(), self._loop)
        try:
            future.result(timeout=60)
        except TimeoutError:
            logger.warning("[MCP] 初始化超时 (60s)")
            return self._tool_modules
        except (OSError, ConnectionError) as e:
            logger.warning(f"[MCP] 连接异常: {e}")
            return self._tool_modules
        except Exception as e:
            logger.warning(f"[MCP] 初始化失败: {e}")
            return self._tool_modules

    def stop_sync(self):
        if not self._loop:
            return
        future = asyncio.run_coroutine_threadsafe(self._stop_all(), self._loop)
        try:
            future.result(timeout=10)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    def sync_call(self, server_name: str, tool_name: str, args: dict) -> str:
        conn = self._connections.get(server_name)
        if not conn or not conn.session:
            return f"[MCP 错误] 服务器 {server_name} 未连接"
        timeout = conn.cfg.get("execute_timeout", _EXECUTE_TIMEOUT)
        future = asyncio.run_coroutine_threadsafe(
            conn.call_tool(tool_name, args), self._loop
        )
        try:
            return future.result(timeout=timeout)
        except asyncio.TimeoutError:
            future.cancel()
            return f"[MCP 错误] 工具调用超时 ({timeout}s)"
        except (OSError, ConnectionError) as e:
            return f"[MCP 错误] 连接失败: {e}"
        except Exception as e:
            return f"[MCP 错误] 调用失败: {e}"

    def get_tool_modules(self) -> list[_MCPToolModule]:
        return self._tool_modules

    async def _start_all(self):
        for server_name, cfg in _MCP_SERVERS.items():
            conn = MCPConnection(server_name, cfg)
            success = await conn.connect()
            if success and conn.tools:
                self._connections[server_name] = conn
                for tool in conn.tools:
                    mod = self._make_wrapper(conn, server_name, tool)
                    self._tool_modules.append(mod)
        logger.info(f"[MCP] 共加载 {len(self._tool_modules)} 个工具，{len(self._connections)} 个服务器")

    async def _stop_all(self):
        for conn in self._connections.values():
            await conn.disconnect()
        self._connections.clear()

    def _make_wrapper(self, conn: MCPConnection, server_name: str, tool) -> _MCPToolModule:
        orig_name = tool.name
        mapped_name = f"mcp_{server_name}_{orig_name}"
        schema = tool.inputSchema or {"type": "object", "properties": {}}
        definition = {
            "type": "function",
            "function": {
                "name": mapped_name,
                "description": tool.description or f"MCP tool: {orig_name}",
                "parameters": schema,
            },
        }
        return _MCPToolModule(definition, self, server_name, mapped_name, orig_name)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._started.set()
            self._loop.run_forever()
        finally:
            self._loop.close()
