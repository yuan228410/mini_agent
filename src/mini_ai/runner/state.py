"""工具循环状态管理

管理工具执行循环的状态，包括：
- 当前轮次
- 最大轮次
- 连续错误计数
- 队友 spawn 状态
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopState:
    """工具循环状态
    
    Attributes:
        turn: 当前轮次
        max_turns: 最大轮次
        consecutive_errors: 连续错误计数
        spawned_teammate: 是否已 spawn 队友
        messages: 消息列表（引用）
    """
    
    turn: int = 0
    max_turns: int = 20
    consecutive_errors: int = 0
    spawned_teammate: bool = False
    messages: list[dict] = field(default_factory=list)
    
    def should_continue(self) -> bool:
        """是否应该继续循环
        
        只检查轮次，错误检查由外部处理。
        
        Returns:
            True 如果应该继续，False 如果应该停止
        """
        return self.turn < self.max_turns
    
    def increment_turn(self) -> None:
        """递增轮次"""
        self.turn += 1
    
    def record_error(self) -> None:
        """记录错误"""
        self.consecutive_errors += 1
    
    def clear_errors(self) -> None:
        """清除错误计数"""
        self.consecutive_errors = 0
    
    def mark_spawned(self) -> None:
        """标记已 spawn 队友"""
        self.spawned_teammate = True
    
    def stats(self) -> dict[str, Any]:
        """返回状态统计"""
        return {
            "turn": self.turn,
            "max_turns": self.max_turns,
            "consecutive_errors": self.consecutive_errors,
            "spawned_teammate": self.spawned_teammate,
            "message_count": len(self.messages),
        }
