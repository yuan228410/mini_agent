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
    if name in ("build_session_runtime", "build_settings_snapshot"):
        from .runtime_factory import build_session_runtime, build_settings_snapshot
        return {"build_session_runtime": build_session_runtime, "build_settings_snapshot": build_settings_snapshot}[name]
    if name in ("ChatMessage", "MessageRole", "normalize_messages", "to_provider_messages"):
        from .messages import ChatMessage, MessageRole, normalize_messages, to_provider_messages
        return {"ChatMessage": ChatMessage, "MessageRole": MessageRole, "normalize_messages": normalize_messages, "to_provider_messages": to_provider_messages}[name]
    if name in ("ToolCall", "ToolFunctionCall", "ToolResult"):
        from .tool_models import ToolCall, ToolFunctionCall, ToolResult
        return {"ToolCall": ToolCall, "ToolFunctionCall": ToolFunctionCall, "ToolResult": ToolResult}[name]
    if name in ("DisplayEvent", "DisplayEventType", "TERMINAL_EVENT_TYPES"):
        from .events import DisplayEvent, DisplayEventType, TERMINAL_EVENT_TYPES
        return {"DisplayEvent": DisplayEvent, "DisplayEventType": DisplayEventType, "TERMINAL_EVENT_TYPES": TERMINAL_EVENT_TYPES}[name]
    if name in (
        "SettingsSnapshot", "ModelSettings", "TimeoutSettings", "RunnerSettings",
        "CompactorSettings", "DisplaySettings", "ToolSettings", "TeamSettings", "WorkflowSettings",
        "WebSettings", "McpSettings", "ImageSettings", "DatabaseSettings", "DatabaseHistorySettings",
    ):
        from .settings import (
            SettingsSnapshot, ModelSettings, TimeoutSettings, RunnerSettings,
            CompactorSettings, DisplaySettings, ToolSettings, TeamSettings, WorkflowSettings,
            WebSettings, McpSettings, ImageSettings, DatabaseSettings, DatabaseHistorySettings,
        )
        return {
            "SettingsSnapshot": SettingsSnapshot,
            "ModelSettings": ModelSettings,
            "TimeoutSettings": TimeoutSettings,
            "RunnerSettings": RunnerSettings,
            "CompactorSettings": CompactorSettings,
            "DisplaySettings": DisplaySettings,
            "ToolSettings": ToolSettings,
            "TeamSettings": TeamSettings,
            "WorkflowSettings": WorkflowSettings,
            "WebSettings": WebSettings,
            "McpSettings": McpSettings,
            "ImageSettings": ImageSettings,
            "DatabaseSettings": DatabaseSettings,
            "DatabaseHistorySettings": DatabaseHistorySettings,
        }[name]
    if name in ("ExecutionBudget", "CancellationToken"):
        from .execution import ExecutionBudget, CancellationToken
        return {"ExecutionBudget": ExecutionBudget, "CancellationToken": CancellationToken}[name]
    if name in ("UsageCollector", "UsageSnapshot"):
        from .usage import UsageCollector, UsageSnapshot
        return {"UsageCollector": UsageCollector, "UsageSnapshot": UsageSnapshot}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DisplayProtocol", "HistoryPersister", "ChatSession",
    "ApplicationService", "RunTurnOptions", "RunTurnResult",
    "build_tool_registry", "build_session_runtime", "build_settings_snapshot",
    "ChatMessage", "MessageRole", "normalize_messages", "to_provider_messages",
    "ToolCall", "ToolFunctionCall", "ToolResult",
    "DisplayEvent", "DisplayEventType", "TERMINAL_EVENT_TYPES",
    "SettingsSnapshot", "ModelSettings", "TimeoutSettings", "RunnerSettings",
    "CompactorSettings", "DisplaySettings", "ToolSettings", "TeamSettings", "WorkflowSettings",
    "WebSettings", "McpSettings", "ImageSettings", "DatabaseSettings", "DatabaseHistorySettings",
    "ExecutionBudget", "CancellationToken", "UsageCollector", "UsageSnapshot",
]
