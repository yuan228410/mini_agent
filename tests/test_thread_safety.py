"""线程安全测试用例"""
import threading
import time
import pytest
from pathlib import Path
import tempfile
import shutil

# 导入被测试模块
from mini_ai.web.routes.chat import (
    _get_or_create_components,
    _SESSION_COMPONENTS,
    _sessions_lock,
    _cache_key,
)
from mini_ai.team.blackboard import Blackboard
from mini_ai.memory.history_db import HistoryDB, HistoryDBPool


class TestConcurrentSessionCreation:
    """测试并发创建会话"""

    def test_concurrent_get_or_create_components(self):
        """多个线程同时创建同一会话，应获得同一个组件实例"""
        results = []
        errors = []
        
        def create_session():
            try:
                comp = _get_or_create_components(
                    username="test_user",
                    sid="test_session_1",
                    base=None,
                    workspace="default"
                )
                results.append(id(comp))
            except Exception as e:
                errors.append(e)
        
        # 清理之前的测试数据
        cache_key = _cache_key("test_user", "default", "test_session_1")
        with _sessions_lock:
            _SESSION_COMPONENTS.pop(cache_key, None)
        
        # 创建 10 个线程并发请求
        threads = [threading.Thread(target=create_session) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 所有线程应获得同一个组件实例
        assert len(set(results)) == 1, f"发现 {len(set(results))} 个不同实例，应只有一个"
        assert len(errors) == 0, f"发生错误: {errors}"


class TestConcurrentBlackboardWrite:
    """测试黑板并发写入"""

    def test_concurrent_blackboard_operations(self):
        """多个线程并发写入黑板，所有写入都应成功"""
        bb = Blackboard()
        
        def write_values(prefix: str):
            for i in range(100):
                bb.put(f"{prefix}_{i}", f"value_{i}", author=prefix)
        
        # 5 个线程，每个写入 100 个键值对
        threads = [
            threading.Thread(target=write_values, args=(f"t{j}",))
            for j in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 验证所有写入都成功
        for j in range(5):
            for i in range(100):
                value = bb.get(f"t{j}_{i}")
                assert value == f"value_{i}", f"t{j}_{i} 预期 'value_{i}'，实际 '{value}'"


class TestBlackboardAtomicPersist:
    """测试黑板原子写入"""

    def test_blackboard_persist_atomic(self):
        """测试黑板持久化的原子性"""
        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "blackboard.json"
            bb = Blackboard(persist_path=persist_path)
            
            # 写入数据
            bb.put("key1", "value1")
            bb.put("key2", "value2")
            
            # 检查文件存在
            assert persist_path.exists()
            
            # 检查临时文件不存在
            temp_path = persist_path.with_suffix('.tmp')
            assert not temp_path.exists()
            
            # 验证数据完整性
            import json
            data = json.loads(persist_path.read_text())
            assert "key1" in data
            assert "key2" in data


class TestHistoryDBConcurrency:
    """测试数据库并发访问"""

    def test_concurrent_db_writes(self):
        """多个线程并发写入数据库"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = HistoryDB(db_path)
            
            errors = []
            
            def write_messages(prefix: str):
                try:
                    for i in range(50):
                        db.append(
                            workspace="test_ws",
                            session_id="test_session",
                            role="user",
                            content=f"{prefix}_message_{i}"
                        )
                except Exception as e:
                    errors.append(e)
            
            # 5 个线程并发写入
            threads = [
                threading.Thread(target=write_messages, args=(f"t{j}",))
                for j in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            # 不应有错误
            assert len(errors) == 0, f"发生错误: {errors}"
            
            # 验证总消息数
            messages = db.load_session("test_ws", "test_session")
            assert len(messages) == 250  # 5 threads * 50 messages
            
            db.close()

    def test_history_db_pool_thread_safety(self):
        """测试数据库连接池的线程安全性"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 临时修改 user_data_dir 返回值
            import mini_ai.config
            original_user_data_dir = mini_ai.config.user_data_dir
            
            def mock_user_data_dir(username: str):
                return Path(tmpdir) / username
            
            mini_ai.config.user_data_dir = mock_user_data_dir
            
            # 清空连接池
            original_pool = HistoryDBPool._pools.copy()
            HistoryDBPool._pools.clear()
            
            try:
                errors = []
                connections = []
                
                def get_connection():
                    try:
                        db = HistoryDBPool.get("test_user")
                        connections.append(id(db))
                        # 执行简单查询
                        db.list_sessions("test_ws")
                    except Exception as e:
                        errors.append(e)
                
                # 10 个线程并发获取连接
                threads = [threading.Thread(target=get_connection) for _ in range(10)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                
                # 应该只有一个连接实例（共享）
                assert len(set(connections)) == 1, "所有线程应共享同一个连接实例"
                assert len(errors) == 0, f"发生错误: {errors}"
                
            finally:
                # 恢复原始状态
                mini_ai.config.user_data_dir = original_user_data_dir
                HistoryDBPool._pools.clear()
                HistoryDBPool._pools.update(original_pool)


class TestSessionEviction:
    """测试会话淘汰机制"""

    def test_session_eviction_thread_safety(self):
        """测试会话淘汰时的线程安全性"""
        from mini_ai.web.routes.chat import (
            _touch_session,
            _SESSION_ACCESS,
            _MAX_CACHED_SESSIONS,
        )
        
        # 临时降低最大缓存数以触发淘汰
        original_max = _MAX_CACHED_SESSIONS
        import mini_ai.web.routes.chat as chat_module
        chat_module._MAX_CACHED_SESSIONS = 5
        
        errors = []
        
        def touch_sessions():
            try:
                for i in range(20):
                    cache_key = f"test_user:default:session_{threading.current_thread().name}_{i}"
                    _touch_session(cache_key)
            except Exception as e:
                errors.append(e)
        
        try:
            # 5 个线程并发触发淘汰
            threads = [
                threading.Thread(target=touch_sessions, name=f"t{j}")
                for j in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            # 不应有 RuntimeError（字典修改错误）
            assert len(errors) == 0, f"发生错误: {errors}"
            
            # 缓存数量应不超过限制
            assert len(_SESSION_ACCESS) <= 10  # 留一些余量
            
        finally:
            # 恢复原始限制
            chat_module._MAX_CACHED_SESSIONS = original_max


# 运行测试
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
