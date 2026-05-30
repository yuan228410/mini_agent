"""Team 协作子包 — 多 Agent 编排系统"""
from .bus import MessageBus
from .manager import TeammateManager
from .loop import wait_for_teammates, shutdown_teammates, cleanup_inbox
from .blackboard import Blackboard
from .task_graph import TaskGraph, TaskNode
from .orchestrator import Orchestrator

REPLY_INSTRUCTION = "队友回禀已收到。先 blackboard_read 读黑板结果，再回复。"
