"""异步数据库写入器测试"""
import os
import signal
import tempfile
import threading
import time
from pathlib import Path

import pytest

from .async_db_writer import AsyncDBWriter, WriteTask
from .history_db import HistoryDB


class TestAsyncDBWriter:
    """AsyncDBWriter 单元测试"""
    
    def test_basic_write(self, tmp_path):
        """测试基本写入功能"""
        db_path = tmp_path / "test.db"
        
        # 创建写入器
        writer = AsyncDBWriter(db_path)
        writer.start()
        
        # 初始化数据库结构
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content=messages, content_rowid=id)")
        conn.close()
        
        # 提交写入任务
        task_id = writer.submit_write(
            workspace="test_ws",
            session_id="test_session",
            role="user",
            content="Hello, world!",
            metadata=""
        )
        
        assert task_id > 0
        
        # 等待写入完成
        writer.flush(timeout=2.0)
        
        # 验证数据
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT workspace, session_id, role, content FROM messages").fetchall()
        conn.close()
        
        assert len(rows) == 1
        assert rows[0] == ("test_ws", "test_session", "user", "Hello, world!")
        
        # 停止写入器
        writer.stop()
    
    def test_batch_write(self, tmp_path):
        """测试批量写入功能"""
        db_path = tmp_path / "test_batch.db"
        
        # 创建写入器
        writer = AsyncDBWriter(db_path)
        writer.start()
        
        # 初始化数据库结构
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content=messages, content_rowid=id)")
        conn.close()
        
        # 批量提交
        messages = [
            {"role": "user", "content": f"Message {i}", "metadata": ""}
            for i in range(100)
        ]
        
        count = writer.submit_batch(
            workspace="test_ws",
            session_id="test_session",
            messages=messages
        )
        
        assert count == 100
        
        # 等待写入完成
        writer.flush(timeout=3.0)
        
        # 验证数据
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        conn.close()
        
        assert rows[0] == 100
        
        # 停止写入器
        writer.stop()
    
    def test_read_consistency(self, tmp_path):
        """测试读取一致性（缓存）"""
        db_path = tmp_path / "test_consistency.db"
        
        # 创建写入器
        writer = AsyncDBWriter(db_path)
        writer.start()
        
        # 初始化数据库结构
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content=messages, content_rowid=id)")
        conn.close()
        
        # 提交写入任务
        writer.submit_write(
            workspace="test_ws",
            session_id="test_session",
            role="user",
            content="Cached message",
            metadata=""
        )
        
        # 立即从缓存读取（此时可能还未写入数据库）
        cached_msgs = writer.get_cached_messages("test_ws", "test_session")
        
        assert len(cached_msgs) >= 1
        assert cached_msgs[0]["content"] == "Cached message"
        
        # 等待写入完成
        writer.flush(timeout=2.0)
        
        # 验证数据库也有数据
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT content FROM messages").fetchall()
        conn.close()
        
        assert len(rows) >= 1
        
        # 停止写入器
        writer.stop()
    
    def test_concurrent_writes(self, tmp_path):
        """测试并发写入"""
        db_path = tmp_path / "test_concurrent.db"
        
        # 创建写入器
        writer = AsyncDBWriter(db_path)
        writer.start()
        
        # 初始化数据库结构
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content=messages, content_rowid=id)")
        conn.close()
        
        # 多线程并发写入
        thread_count = 10
        writes_per_thread = 100
        threads = []
        
        def write_thread(thread_id):
            for i in range(writes_per_thread):
                writer.submit_write(
                    workspace="test_ws",
                    session_id=f"session_{thread_id}",
                    role="user",
                    content=f"Thread {thread_id} message {i}",
                    metadata=""
                )
        
        for i in range(thread_count):
            t = threading.Thread(target=write_thread, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # 等待写入完成
        writer.flush(timeout=5.0)
        
        # 验证数据
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        conn.close()
        
        assert rows[0] == thread_count * writes_per_thread
        
        # 停止写入器
        writer.stop()
    
    def test_statistics(self, tmp_path):
        """测试统计功能"""
        db_path = tmp_path / "test_stats.db"
        
        # 创建写入器
        writer = AsyncDBWriter(db_path)
        writer.start()
        
        # 初始化数据库结构
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT DEFAULT ''
            )
        """)
        conn.close()
        
        # 提交写入任务
        for i in range(10):
            writer.submit_write(
                workspace="test_ws",
                session_id="test_session",
                role="user",
                content=f"Message {i}",
                metadata=""
            )
        
        # 等待写入完成
        writer.flush(timeout=2.0)
        
        # 获取统计信息
        stats = writer.get_stats()
        
        assert stats["total_writes"] == 10
        assert stats["batch_writes"] >= 1
        assert stats["is_running"] == True
        
        # 停止写入器
        writer.stop()
        
        stats = writer.get_stats()
        assert stats["is_running"] == False


class TestHistoryDBAsync:
    """HistoryDB 异步写入集成测试"""
    
    def test_async_mode_basic(self, tmp_path):
        """测试异步模式基本功能"""
        db_path = tmp_path / "test_async.db"
        
        # 创建异步模式的 HistoryDB
        db = HistoryDB(db_path, async_write=True)
        
        # 写入消息
        db.append(
            workspace="test_ws",
            session_id="test_session",
            role="user",
            content="Hello, async!",
            metadata=""
        )
        
        # 等待写入完成
        db.flush(timeout=2.0)
        
        # 读取消息
        messages = db.load_session("test_ws", "test_session")
        
        assert len(messages) >= 1
        assert messages[0]["content"] == "Hello, async!"
        
        # 获取统计信息
        stats = db.get_async_stats()
        assert stats["total_writes"] >= 1
        
        # 关闭
        db.close()
    
    def test_sync_vs_async(self, tmp_path):
        """对比同步和异步模式"""
        import time
        
        # 同步模式
        sync_db = HistoryDB(tmp_path / "sync.db", async_write=False)
        
        start = time.time()
        for i in range(100):
            sync_db.append(
                workspace="test_ws",
                session_id="test_session",
                role="user",
                content=f"Message {i}",
                metadata=""
            )
        sync_time = time.time() - start
        
        # 异步模式
        async_db = HistoryDB(tmp_path / "async.db", async_write=True)
        
        start = time.time()
        for i in range(100):
            async_db.append(
                workspace="test_ws",
                session_id="test_session",
                role="user",
                content=f"Message {i}",
                metadata=""
            )
        async_time = time.time() - start
        
        # 等待异步写入完成
        async_db.flush(timeout=3.0)
        
        # 异步模式应该更快
        print(f"\n同步模式: {sync_time:.4f}s")
        print(f"异步模式: {async_time:.4f}s")
        print(f"性能提升: {(sync_time - async_time) / sync_time * 100:.1f}%")
        
        # 验证数据
        sync_msgs = sync_db.load_session("test_ws", "test_session")
        async_msgs = async_db.load_session("test_ws", "test_session")
        
        assert len(sync_msgs) == 100
        assert len(async_msgs) == 100
        
        sync_db.close()
        async_db.close()
    
    def test_read_consistency_integration(self, tmp_path):
        """测试读取一致性集成"""
        db_path = tmp_path / "test_consistency.db"
        
        # 创建异步模式的 HistoryDB
        db = HistoryDB(db_path, async_write=True)
        
        # 写入消息
        db.append(
            workspace="test_ws",
            session_id="test_session",
            role="user",
            content="Message 1",
            metadata=""
        )
        
        # 立即读取（应该从缓存读取）
        messages = db.load_session("test_ws", "test_session")
        
        # 应该能读到消息（从缓存）
        assert len(messages) >= 1
        assert "Message 1" in [m["content"] for m in messages]
        
        # 等待写入完成
        db.flush(timeout=2.0)
        
        # 再次读取（应该合并缓存和数据库）
        messages = db.load_session("test_ws", "test_session")
        
        assert len(messages) >= 1
        
        db.close()
    
    def test_batch_write_integration(self, tmp_path):
        """测试批量写入集成"""
        db_path = tmp_path / "test_batch.db"
        
        # 创建异步模式的 HistoryDB
        db = HistoryDB(db_path, async_write=True)
        
        # 批量写入
        messages = [
            {"role": "user", "content": f"Message {i}", "metadata": ""}
            for i in range(50)
        ]
        
        count = db.append_batch(
            workspace="test_ws",
            session_id="test_session",
            messages=messages
        )
        
        assert count == 50
        
        # 等待写入完成
        db.flush(timeout=3.0)
        
        # 读取验证
        loaded = db.load_session("test_ws", "test_session")
        
        assert len(loaded) >= 50
        
        db.close()


class TestAsyncWriterSafety:
    """异步写入器安全性测试"""
    
    def test_graceful_shutdown(self, tmp_path):
        """测试优雅关闭"""
        db_path = tmp_path / "test_shutdown.db"
        
        # 创建写入器
        writer = AsyncDBWriter(db_path)
        writer.start()
        
        # 初始化数据库结构
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT DEFAULT ''
            )
        """)
        conn.close()
        
        # 提交大量写入任务
        for i in range(100):
            writer.submit_write(
                workspace="test_ws",
                session_id="test_session",
                role="user",
                content=f"Message {i}",
                metadata=""
            )
        
        # 停止（应该优雅关闭，写入所有剩余任务）
        writer.stop(timeout=5.0)
        
        # 验证所有消息都已写入
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        conn.close()
        
        assert rows[0] == 100
    
    def test_queue_full_handling(self, tmp_path):
        """测试队列满处理"""
        db_path = tmp_path / "test_queue_full.db"
        
        # 创建小容量队列的写入器（用于测试）
        writer = AsyncDBWriter(db_path)
        writer.QUEUE_MAX_SIZE = 100  # 临时修改
        writer.start()
        
        # 初始化数据库结构
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT DEFAULT ''
            )
        """)
        conn.close()
        
        # 提交大量任务（超过队列容量）
        for i in range(200):
            writer.submit_write(
                workspace="test_ws",
                session_id="test_session",
                role="user",
                content=f"Message {i}",
                metadata=""
            )
        
        # 等待写入完成
        writer.flush(timeout=5.0)
        
        # 验证所有消息都已写入
        stats = writer.get_stats()
        assert stats["total_writes"] == 200
        
        # 验证数据库中的消息数
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        conn.close()
        
        assert rows[0] == 200
        
        writer.stop()


if __name__ == "__main__":
    # 简单的手动测试
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        print("=== 测试基本写入 ===")
        test = TestAsyncDBWriter()
        test.test_basic_write(tmp_path)
        print("✓ 基本写入测试通过")
        
        print("\n=== 测试批量写入 ===")
        test.test_batch_write(tmp_path)
        print("✓ 批量写入测试通过")
        
        print("\n=== 测试并发写入 ===")
        test.test_concurrent_writes(tmp_path)
        print("✓ 并发写入测试通过")
        
        print("\n=== 测试 HistoryDB 异步集成 ===")
        test2 = TestHistoryDBAsync()
        test2.test_async_mode_basic(tmp_path)
        print("✓ 异步集成测试通过")
        
        print("\n=== 对比同步和异步性能 ===")
        test2.test_sync_vs_async(tmp_path)
        print("✓ 性能对比测试通过")
        
        print("\n=== 测试优雅关闭 ===")
        test3 = TestAsyncWriterSafety()
        test3.test_graceful_shutdown(tmp_path)
        print("✓ 优雅关闭测试通过")
        
        print("\n✅ 所有测试通过！")
