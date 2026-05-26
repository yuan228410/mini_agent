"""Team 协作子包 — 多 Agent 编排系统"""
from .bus import MessageBus
from .manager import TeammateManager
from .loop import wait_for_teammates, shutdown_teammates, cleanup_inbox
from .blackboard import Blackboard
from .task_graph import TaskGraph, TaskNode
from .orchestrator import Orchestrator
