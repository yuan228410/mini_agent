"""Web session state models."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..core.runtime_types import MessageDict, MetadataDict, SessionComponents, UsageDict
from ..plan.schema import PlanSessionState

DEFAULT_MAX_CACHED_SESSIONS = 20


@dataclass
class SessionState:
    """一个会话的完整状态（合并原 13 个 dict 中的对应字段）"""

    messages: list[MessageDict] = field(default_factory=list)
    model: str = ""
    status: str = "idle"
    access_time: float = 0.0
    last_usage: UsageDict = field(default_factory=dict)
    plan: PlanSessionState = field(default_factory=PlanSessionState)
    lock: threading.Lock = field(default_factory=threading.Lock)
    abort_event: threading.Event = field(default_factory=threading.Event)
    meta: MetadataDict = field(default_factory=dict)
    refs: int = 0
    components: SessionComponents = field(default_factory=SessionComponents)
