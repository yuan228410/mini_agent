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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["DisplayProtocol", "HistoryPersister", "ChatSession", "ApplicationService", "RunTurnOptions", "RunTurnResult"]
