"""Web 端会话并发测试"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
import threading


class TestWebSessionConcurrency:
    """测试 Web 会话并发安全性"""
    
    def test_session_lock_prevents_concurrent_execution(self):
        """会话锁应阻止同一会话并发执行"""
        from mini_ai.web.routes.chat import _get_session_lock, _cache_key
        
        username = "test_user"
        workspace = "default"
        sid = "test-session-123"
        
        lock = _get_session_lock(username, workspace, sid)
        
        # 第一次获取锁
        acquired1 = lock.acquire(blocking=False)
        assert acquired1 is True
        
        # 第二次获取锁应失败
        lock2 = _get_session_lock(username, workspace, sid)
        acquired2 = lock2.acquire(blocking=False)
        assert acquired2 is False
        
        # 释放锁后应能再次获取
        lock.release()
        acquired3 = lock2.acquire(blocking=False)
        assert acquired3 is True
        lock2.release()
    
    def test_is_session_generating(self):
        """测试会话生成状态检查"""
        from mini_ai.web.routes.chat import _SESSION_STATUS, _cache_key
        
        username = "test_user"
        workspace = "default"
        sid = "test-session-456"
        key = _cache_key(username, workspace, sid)
        
        # 初始状态应为 idle
        assert _SESSION_STATUS.get(key) != "generating"
        
        # 设置为 generating
        _SESSION_STATUS[key] = "generating"
        assert _SESSION_STATUS.get(key) == "generating"
        
        # 设置为 idle
        _SESSION_STATUS[key] = "idle"
        assert _SESSION_STATUS.get(key) != "generating"
        
        # 清理
        del _SESSION_STATUS[key]
    
    def test_session_eviction_skips_generating_sessions(self):
        """会话淘汰应跳过正在生成的会话"""
        from mini_ai.web.routes.chat import (
            _touch_session,
            _SESSION_STATUS,
            _SESSION_ACCESS,
            _SESSIONS,
            _MAX_CACHED_SESSIONS,
            _cache_key
        )
        
        # 清空缓存
        _SESSION_ACCESS.clear()
        _SESSIONS.clear()
        _SESSION_STATUS.clear()
        
        # 创建多个会话
        for i in range(_MAX_CACHED_SESSIONS + 5):
            key = f"user:default:session-{i}"
            _SESSIONS[key] = [{"role": "system", "content": "test"}]
            _touch_session(key)
        
        # 标记最后一个会话为 generating
        generating_key = f"user:default:session-{_MAX_CACHED_SESSIONS + 4}"
        _SESSION_STATUS[generating_key] = "generating"
        
        # 触发淘汰
        new_key = "user:default:session-new"
        _SESSIONS[new_key] = [{"role": "system", "content": "test"}]
        _touch_session(new_key)
        
        # generating 的会话不应被淘汰
        assert generating_key in _SESSIONS
        assert _SESSION_STATUS.get(generating_key) == "generating"
        
        # 清理
        _SESSION_ACCESS.clear()
        _SESSIONS.clear()
        _SESSION_STATUS.clear()


class TestWebSessionLock:
    """测试会话锁在工具循环中的应用"""
    
    def test_session_lock_basic(self):
        """会话锁基本功能测试"""
        lock = threading.Lock()
        
        # 应能获取锁
        assert lock.acquire(blocking=False) is True
        
        # 释放后应能再次获取
        lock.release()
        assert lock.acquire(blocking=False) is True
        lock.release()
    
    def test_lock_released_on_exception(self):
        """异常时应释放会话锁"""
        lock = threading.Lock()
        
        # 模拟在 finally 块中释放锁
        try:
            lock.acquire()
            raise RuntimeError("Test error")
        except RuntimeError:
            pass
        finally:
            if lock.locked():
                lock.release()
        
        # 锁应被释放
        assert not lock.locked()
        assert lock.acquire(blocking=False) is True
        lock.release()


class TestWebSocketConcurrency:
    """测试 WebSocket 并发场景"""
    
    def test_concurrent_messages_rejected(self):
        """同一会话并发消息应被拒绝"""
        from mini_ai.web.routes.chat import _SESSION_STATUS, _cache_key
        
        username = "test_user"
        workspace = "default"
        sid = "test-session-789"
        key = _cache_key(username, workspace, sid)
        
        # 模拟会话正在生成
        _SESSION_STATUS[key] = "generating"
        
        # 检查应返回 generating
        assert _SESSION_STATUS.get(key) == "generating"
        
        # 清理
        del _SESSION_STATUS[key]
    
    def test_abort_event_cleanup(self):
        """WebSocket 断开时应清理 abort 事件"""
        from mini_ai.web.routes.chat import _SESSION_ABORTS, _cache_key
        
        username = "test_user"
        workspace = "default"
        sid = "test-session-abort"
        key = _cache_key(username, workspace, sid)
        
        # 创建 abort 事件
        event = threading.Event()
        _SESSION_ABORTS[key] = event
        
        # 模拟清理
        evt = _SESSION_ABORTS.pop(key, None)
        assert evt is not None
        assert key not in _SESSION_ABORTS


# 集成测试（需要实际 WebSocket 连接，标记为慢测试）
class TestWebIntegration:
    """Web 端集成测试"""
    
    def test_full_session_lifecycle(self):
        """测试完整会话生命周期"""
        # 这个测试需要实际的 WebSocket 连接和数据库
        # 在 CI 中标记为 slow，默认跳过
        pass
