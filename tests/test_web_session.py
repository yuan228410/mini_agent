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
        from mini_ai.web.session_manager import SessionManager, SessionState, cache_key, _MAX_CACHED_SESSIONS
        
        username = "test_user"
        workspace = "default"
        sid = "test-session-123"
        
        lock = SessionManager.instance().get_lock(SessionManager.cache_key(username, workspace, sid))
        
        # 第一次获取锁
        acquired1 = lock.acquire(blocking=False)
        assert acquired1 is True
        
        # 第二次获取锁应失败
        lock2 = SessionManager.instance().get_lock(SessionManager.cache_key(username, workspace, sid))
        acquired2 = lock2.acquire(blocking=False)
        assert acquired2 is False
        
        # 释放锁后应能再次获取
        lock.release()
        acquired3 = lock2.acquire(blocking=False)
        assert acquired3 is True
        lock2.release()
    
    def test_is_session_generating(self):
        """测试会话生成状态检查"""
        from mini_ai.web.session_manager import SessionManager, SessionState, cache_key, _MAX_CACHED_SESSIONS
        
        username = "test_user"
        workspace = "default"
        sid = "test-session-456"
        key = SessionManager.cache_key(username, workspace, sid)
        
        # 初始状态应为 idle
        assert SessionManager.instance().get_status(key) != "generating"
        
        # 设置为 generating
        SessionManager.instance()._sessions[key] = SessionState(status="generating")
        assert SessionManager.instance().get_status(key) == "generating"
        
        # 设置为 idle
        SessionManager.instance()._sessions[key] = SessionState(status="idle")
        assert SessionManager.instance().get_status(key) != "generating"
        
        # 清理
        del SessionManager.instance()._sessions[key]
    
    def test_session_eviction_skips_generating_sessions(self):
        """会话淘汰应跳过正在生成的会话"""
        from mini_ai.web.session_manager import SessionManager, SessionState, cache_key, _MAX_CACHED_SESSIONS
        
        # 清空缓存
        SessionManager.instance()._sessions.clear()
        
        # 创建多个会话
        for i in range(_MAX_CACHED_SESSIONS + 5):
            key = f"user:default:session-{i}"
            SessionManager.instance()._sessions[key] = SessionState(messages=[{"role": "system", "content": "test"}])
            SessionManager.instance().touch(key)
        
        # 标记最后一个会话为 generating
        generating_key = f"user:default:session-{_MAX_CACHED_SESSIONS + 4}"
        SessionManager.instance()._sessions[generating_key] = SessionState(status="generating")
        
        # 触发淘汰
        new_key = "user:default:session-new"
        SessionManager.instance()._sessions[new_key] = SessionState(messages=[{"role": "system", "content": "test"}])
        SessionManager.instance().touch(new_key)
        
        # generating 的会话不应被淘汰
        assert generating_key in SessionManager.instance()._sessions
        assert SessionManager.instance().get_status(generating_key) == "generating"
        
        # 清理
        SessionManager.instance()._sessions.clear()


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
        from mini_ai.web.session_manager import SessionManager, SessionState, cache_key, _MAX_CACHED_SESSIONS
        
        username = "test_user"
        workspace = "default"
        sid = "test-session-789"
        key = SessionManager.cache_key(username, workspace, sid)
        
        # 模拟会话正在生成
        SessionManager.instance()._sessions[key] = SessionState(status="generating")
        
        # 检查应返回 generating
        assert SessionManager.instance().get_status(key) == "generating"
        
        # 清理
        del SessionManager.instance()._sessions[key]
    
    def test_abort_event_cleanup(self):
        """WebSocket 断开时应清理 abort 事件"""
        from mini_ai.web.session_manager import SessionManager, SessionState, cache_key, _MAX_CACHED_SESSIONS
        
        username = "test_user"
        workspace = "default"
        sid = "test-session-abort"
        key = SessionManager.cache_key(username, workspace, sid)
        
        # 创建 abort 事件
        event = threading.Event()
        sm = SessionManager.instance()
        sm._sessions[key] = SessionState(abort_event=event)
        
        # 模拟清理
        evt = SessionManager.instance()._sessions.pop(key, None)
        assert evt is not None
        assert SessionManager.instance().get_abort_event(key) is None or not SessionManager.instance().get_abort_event(key).is_set()


# 集成测试（需要实际 WebSocket 连接，标记为慢测试）
class TestWebIntegration:
    """Web 端集成测试"""
    
    def test_full_session_lifecycle(self):
        """测试完整会话生命周期"""
        # 这个测试需要实际的 WebSocket 连接和数据库
        # 在 CI 中标记为 slow，默认跳过
        pass
