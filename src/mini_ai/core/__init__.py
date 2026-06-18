"""核心编排层 — CLI/Web 共用的会话逻辑、类型协议、持久化"""
from .display_protocol import DisplayProtocol
from .persister import HistoryPersister

# ChatSession 延迟导入（避免 runner ↔ core 循环依赖）
def __getattr__(name):
    if name == "ChatSession":
        from .chat_session import ChatSession
        return ChatSession
    if name in ("ApplicationService", "RunTurnOptions", "RunTurnResult"):
        from .application_service import ApplicationService, RunTurnOptions, RunTurnResult
        return {"ApplicationService": ApplicationService, "RunTurnOptions": RunTurnOptions, "RunTurnResult": RunTurnResult}[name]
    if name == "build_tool_registry":
        from .tool_registry_factory import build_tool_registry
        return build_tool_registry
    if name == "build_session_runtime":
        from .runtime_factory import build_session_runtime
        return build_session_runtime
    if name in ("ChatMessage", "MessageRole", "normalize_messages", "to_provider_messages"):
        from .messages import ChatMessage, MessageRole, normalize_messages, to_provider_messages
        return {"ChatMessage": ChatMessage, "MessageRole": MessageRole, "normalize_messages": normalize_messages, "to_provider_messages": to_provider_messages}[name]
    if name in ("ToolCall", "ToolFunctionCall", "ToolResult"):
        from .tool_models import ToolCall, ToolFunctionCall, ToolResult
        return {"ToolCall": ToolCall, "ToolFunctionCall": ToolFunctionCall, "ToolResult": ToolResult}[name]
    if name in ("DisplayEvent", "DisplayEventType", "TERMINAL_EVENT_TYPES"):
        from .events import DisplayEvent, DisplayEventType, TERMINAL_EVENT_TYPES
        return {"DisplayEvent": DisplayEvent, "DisplayEventType": DisplayEventType, "TERMINAL_EVENT_TYPES": TERMINAL_EVENT_TYPES}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DisplayProtocol", "HistoryPersister", "ChatSession",
    "ApplicationService", "RunTurnOptions", "RunTurnResult",
    "build_tool_registry", "build_session_runtime",
    "ChatMessage", "MessageRole", "normalize_messages", "to_provider_messages",
    "ToolCall", "ToolFunctionCall", "ToolResult",
    "DisplayEvent", "DisplayEventType", "TERMINAL_EVENT_TYPES",
]
