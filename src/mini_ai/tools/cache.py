"""工具结果缓存

提供线程安全的 LRU 缓存，减少同一对话中重复工具调用的开销。
"""
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..logger import logger


@dataclass
class CacheEntry:
    """缓存条目"""
    result: Any
    timestamp: float


class ToolCache:
    """线程安全的工具结果缓存
    
    特性：
    - LRU 淘汰策略
    - TTL 过期机制
    - 黑名单过滤（有副作用的工具不缓存）
    """
    
    # 不应缓存的工具（有副作用）
    BLACKLIST = {
        # 文件写入操作
        "write_file", "edit_file", "delete_file", "rename_file",
        # 命令执行
        "run_command",
        # 消息发送
        "send_message", "broadcast",
        # 黑板写入
        "blackboard_write",
        # 记忆管理
        "remember", "forget",
        # 技能管理
        "install_skill", "delete_skill",
        # 配置修改
        # config 的 write 操作有副作用，read/list/reload 可缓存，在 get/set 中动态判断
        # 历史管理
        "manage_history",
        # 队友管理
        "spawn_teammate", "dismiss_team",
        # 工作流（触发执行）
        "run_workflow", "workflow_status",
        # 子代理注册（修改全局状态）
        "register_subagent",
    }
    
    def __init__(self, maxsize: int = 100, ttl_seconds: float = 300):
        """
        Args:
            maxsize: 最大缓存条目数
            ttl_seconds: 缓存过期时间（秒）
        """
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0
    
    def _key(self, tool_name: str, args: dict) -> str:
        """生成缓存 key（优化版：快速路径 + 降级方案）
        
        Args:
            tool_name: 工具名称
            args: 工具参数
        
        Returns:
            缓存 key
        """
        # 快速路径：对于简单参数（只有字符串/数字/布尔值），直接拼接
        # 避免不必要的 JSON 序列化开销
        if args and all(isinstance(v, (str, int, float, bool, type(None))) for v in args.values()):
            # 使用 frozenset 保证顺序无关
            try:
                key_str = f"{tool_name}:{frozenset(args.items())}"
                # 只对最终字符串做 hash，避免对每个值单独 hash
                return f"{tool_name}:{hashlib.md5(key_str.encode()).hexdigest()[:12]}"
            except (TypeError, ValueError):
                pass  # 降级到 JSON 方案
        
        # 通用方案：JSON 序列化（支持复杂嵌套结构）
        args_json = json.dumps(args, sort_keys=True, default=str)
        args_hash = hashlib.md5(args_json.encode()).hexdigest()[:12]
        return f"{tool_name}:{args_hash}"
    
    def get(self, tool_name: str, args: dict) -> tuple[Any, bool]:
        """获取缓存
        
        Args:
            tool_name: 工具名称
            args: 工具参数
        
        Returns:
            (result, hit) - result 为缓存结果（未命中时为 None），hit 表示是否命中
        """
        # 黑名单工具不缓存
        if tool_name in self.BLACKLIST:
            with self._lock:
                self._misses += 1
            return None, False
        # config 的 write 操作不缓存，read/list/reload 可缓存
        if tool_name == "config" and args.get("action") == "write":
            with self._lock:
                self._misses += 1
            return None, False
        
        key = self._key(tool_name, args)
        
        with self._lock:
            entry = self._cache.get(key)
            
            if entry:
                # 检查是否过期
                if time.time() - entry.timestamp < self._ttl:
                    self._hits += 1
                    logger.debug(f"[Cache] 命中: {tool_name}")
                    return entry.result, True
                else:
                    # 过期，删除
                    del self._cache[key]
            self._misses += 1
        
        return None, False
    
    def set(self, tool_name: str, args: dict, result: Any):
        """设置缓存
        
        Args:
            tool_name: 工具名称
            args: 工具参数
            result: 工具结果
        """
        # 黑名单工具不缓存
        if tool_name in self.BLACKLIST:
            return
        # config write 不缓存，且清除该工具所有缓存
        if tool_name == "config" and args.get("action") == "write":
            self._invalidate_prefix("config")
            return
        
        # 结果太大也不缓存（超过 1MB）
        try:
            result_size = len(json.dumps(result, default=str))
            if result_size > 1024 * 1024:  # 1MB
                logger.debug(f"[Cache] 跳过（结果太大 {result_size} 字节）: {tool_name}")
                return
        except (TypeError, ValueError):
            pass  # 无法序列化，也不缓存
        
        key = self._key(tool_name, args)
        
        with self._lock:
            event = self._pending.pop(key, None)
            # LRU 淘汰
            if len(self._cache) >= self._maxsize:
                oldest_key = min(
                    self._cache.keys(),
                    key=lambda k: self._cache[k].timestamp
                )
                del self._cache[oldest_key]
                logger.debug(f"[Cache] LRU 淘汰: {oldest_key}")
            
            self._cache[key] = CacheEntry(result, time.time())
        
        if event:
            event.set()
    
    def get_or_wait(self, tool_name: str, args: dict, timeout: float = 30.0) -> tuple[Any, bool]:
        """获取缓存或等待其他线程计算完成

        首次请求返回 (None, False) 表示需要执行；
        后续并发请求等待首次完成后返回缓存结果。

        Args:
            tool_name: 工具名称
            args: 工具参数
            timeout: 等待超时（秒）

        Returns:
            (result, hit)
        """
        key = self._key(tool_name, args)

        with self._lock:
            # 已有缓存
            entry = self._cache.get(key)
            if entry and time.time() - entry.timestamp < self._ttl:
                self._hits += 1
                return entry.result, True

            # 已有其他线程在计算
            if key in self._pending:
                event = self._pending[key]
            else:
                # 首次请求，注册 pending
                self._pending[key] = threading.Event()
                self._misses += 1
                return None, False

        # 等待计算完成（在锁外等待，避免死锁）
        event.wait(timeout=timeout)

        with self._lock:
            entry = self._cache.get(key)
            if entry and time.time() - entry.timestamp < self._ttl:
                self._hits += 1
                return entry.result, True
            # 等待超时或执行失败，清理 pending 状态
            self._pending.pop(key, None)
            self._misses += 1

        return None, False

    def mark_done(self, tool_name: str, args: dict, result: Any):
        """写入缓存并通知等待线程
        
        即使结果为 None 或执行失败，也应调用此方法清理 pending 状态
        """
        key = self._key(tool_name, args)
        with self._lock:
            # 只有非 None 结果才缓存
            if result is not None:
                self._cache[key] = CacheEntry(result, time.time())
            event = self._pending.pop(key, None)
        if event:
            event.set()
    
    def mark_failed(self, tool_name: str, args: dict):
        """标记执行失败，清理 pending 状态并通知等待线程"""
        key = self._key(tool_name, args)
        with self._lock:
            event = self._pending.pop(key, None)
        if event:
            event.set()

    def _invalidate_prefix(self, prefix: str) -> int:
        with self._lock:
            keys_to_del = [k for k in self._cache if k.startswith(prefix + ":")]
            for k in keys_to_del:
                del self._cache[k]
            if keys_to_del:
                logger.debug(f"[Cache] 清除 {prefix} 相关缓存: {len(keys_to_del)} 条")
        return len(keys_to_del)

    def clear(self):
        """清空缓存（新对话时调用）"""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            logger.debug("[Cache] 已清空")
    
    def stats(self) -> dict:
        """获取缓存统计
        
        Returns:
            统计信息字典
        """
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0
            
            return {
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.1%}",
            }


# 全局缓存实例
_tool_cache: ToolCache | None = None
_cache_lock = threading.Lock()


def get_tool_cache() -> ToolCache:
    """获取全局缓存实例（线程安全）"""
    global _tool_cache
    if _tool_cache is None:
        with _cache_lock:
            # 双重检查
            if _tool_cache is None:
                _tool_cache = ToolCache()
    return _tool_cache


def clear_tool_cache():
    """清空全局缓存"""
    cache = get_tool_cache()
    cache.clear()
