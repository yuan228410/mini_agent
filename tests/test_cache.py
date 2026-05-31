"""测试工具结果缓存"""
import time
import pytest
from mini_ai.tools.cache import ToolCache, get_tool_cache, clear_tool_cache


class TestToolCache:
    """测试工具缓存核心功能"""
    
    def test_cache_hit(self):
        """缓存命中"""
        cache = ToolCache()
        
        # 设置缓存
        cache.set("read_file", {"path": "/test.txt"}, "file content")
        
        # 获取缓存
        result, hit = cache.get("read_file", {"path": "/test.txt"})
        
        assert hit is True
        assert result == "file content"
    
    def test_cache_miss(self):
        """缓存未命中"""
        cache = ToolCache()
        
        result, hit = cache.get("read_file", {"path": "/test.txt"})
        
        assert hit is False
        assert result is None
    
    def test_blacklist_tools_not_cached(self):
        """黑名单工具不缓存"""
        cache = ToolCache()
        
        # 写入工具在黑名单中
        cache.set("write_file", {"path": "/test.txt"}, "written")
        
        result, hit = cache.get("write_file", {"path": "/test.txt"})
        
        assert hit is False
        assert result is None
    
    def test_cache_different_args(self):
        """不同参数有不同缓存"""
        cache = ToolCache()
        
        cache.set("read_file", {"path": "/a.txt"}, "content A")
        cache.set("read_file", {"path": "/b.txt"}, "content B")
        
        result_a, hit_a = cache.get("read_file", {"path": "/a.txt"})
        result_b, hit_b = cache.get("read_file", {"path": "/b.txt"})
        
        assert hit_a is True
        assert result_a == "content A"
        assert hit_b is True
        assert result_b == "content B"
    
    def test_cache_ttl_expiration(self):
        """TTL 过期机制"""
        cache = ToolCache(ttl_seconds=0.1)  # 0.1 秒过期
        
        cache.set("read_file", {"path": "/test.txt"}, "content")
        
        # 立即获取，应该命中
        result1, hit1 = cache.get("read_file", {"path": "/test.txt"})
        assert hit1 is True
        
        # 等待过期
        time.sleep(0.15)
        
        # 再次获取，应该未命中
        result2, hit2 = cache.get("read_file", {"path": "/test.txt"})
        assert hit2 is False
    
    def test_cache_lru_eviction(self):
        """LRU 淘汰策略"""
        cache = ToolCache(maxsize=3)
        
        # 填满缓存
        cache.set("read_file", {"path": "/1.txt"}, "1")
        cache.set("read_file", {"path": "/2.txt"}, "2")
        cache.set("read_file", {"path": "/3.txt"}, "3")
        
        # 再添加一个，应该淘汰最旧的
        cache.set("read_file", {"path": "/4.txt"}, "4")
        
        stats = cache.stats()
        assert stats["size"] == 3
        
        # 第一个应该被淘汰
        result, hit = cache.get("read_file", {"path": "/1.txt"})
        assert hit is False
        
        # 最新的应该存在
        result, hit = cache.get("read_file", {"path": "/4.txt"})
        assert hit is True
    
    def test_cache_clear(self):
        """清空缓存"""
        cache = ToolCache()
        
        cache.set("read_file", {"path": "/test.txt"}, "content")
        cache.set("search_files", {"pattern": "test"}, "results")
        
        cache.clear()
        
        stats = cache.stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
    
    def test_cache_stats(self):
        """缓存统计"""
        cache = ToolCache()
        
        # 未命中
        cache.get("read_file", {"path": "/a.txt"})
        
        # 设置并命中
        cache.set("read_file", {"path": "/b.txt"}, "content")
        cache.get("read_file", {"path": "/b.txt"})
        cache.get("read_file", {"path": "/b.txt"})
        
        stats = cache.stats()
        
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == "66.7%"
    
    def test_cache_large_result_skipped(self):
        """大结果不缓存"""
        cache = ToolCache()
        
        # 创建超过 1MB 的结果
        large_content = "x" * (1024 * 1024 + 1)
        cache.set("read_file", {"path": "/large.txt"}, large_content)
        
        result, hit = cache.get("read_file", {"path": "/large.txt"})
        
        assert hit is False


class TestGlobalCache:
    """测试全局缓存实例"""
    
    def test_get_tool_cache_singleton(self):
        """全局缓存是单例"""
        cache1 = get_tool_cache()
        cache2 = get_tool_cache()
        
        assert cache1 is cache2
    
    def test_clear_tool_cache(self):
        """清空全局缓存"""
        cache = get_tool_cache()
        cache.set("read_file", {"path": "/test.txt"}, "content")
        
        clear_tool_cache()
        
        stats = cache.stats()
        assert stats["size"] == 0

    def test_config_write_not_cached(self):
        """config write 操作不缓存"""
        cache = ToolCache()
        
        # config write 不缓存
        cache.set("config", {"action": "write", "path": "test", "value": "1"}, "ok")
        
        result, hit = cache.get("config", {"action": "write", "path": "test", "value": "1"})
        assert hit is False
    
    def test_config_read_cached(self):
        """config read 操作可缓存"""
        cache = ToolCache()
        
        # config read 可缓存
        cache.set("config", {"action": "read", "path": "test"}, {"value": "123"})
        
        result, hit = cache.get("config", {"action": "read", "path": "test"})
        assert hit is True
        assert result == {"value": "123"}
    
    def test_config_write_invalidates_config_cache(self):
        """config write 清除所有 config 相关缓存"""
        cache = ToolCache()
        
        # 设置 config read 缓存
        cache.set("config", {"action": "read", "path": "test"}, {"value": "123"})
        cache.set("config", {"action": "list"}, {"models": []})
        
        # config write 应清除所有 config 缓存
        cache.set("config", {"action": "write", "path": "test", "value": "456"}, "ok")
        
        # 之前的缓存应被清除
        result, hit = cache.get("config", {"action": "read", "path": "test"})
        assert hit is False


class TestCacheConcurrency:
    """测试缓存并发安全"""
    
    def test_get_or_wait_basic(self):
        """get_or_wait 基本功能"""
        import threading
        
        cache = ToolCache()
        
        # 首次请求应返回 (None, False)
        result, hit = cache.get_or_wait("read_file", {"path": "/test.txt"})
        assert hit is False
        assert result is None
        
        # 写入缓存
        cache.mark_done("read_file", {"path": "/test.txt"}, "content")
        
        # 再次请求应命中
        result, hit = cache.get_or_wait("read_file", {"path": "/test.txt"})
        assert hit is True
        assert result == "content"
    
    def test_get_or_wait_concurrent(self):
        """get_or_wait 并发等待"""
        import threading
        import time
        
        cache = ToolCache()
        results = []
        
        # 首次请求注册 pending
        result0, hit0 = cache.get_or_wait("read_file", {"path": "/test.txt"})
        assert hit0 is False
        
        def worker():
            result, hit = cache.get_or_wait("read_file", {"path": "/test.txt"}, timeout=5.0)
            results.append((result, hit, threading.current_thread().name))
        
        # 启动多个线程同时请求（它们会等待）
        threads = [threading.Thread(target=worker, name=f"worker-{i}") for i in range(3)]
        for t in threads:
            t.start()
        
        # 等待所有线程进入等待状态
        time.sleep(0.1)
        
        # 主线程写入缓存并通知等待线程
        cache.mark_done("read_file", {"path": "/test.txt"}, "shared content")
        
        # 等待所有线程完成
        for t in threads:
            t.join(timeout=2.0)
        
        # 所有线程应获取到相同结果
        assert len(results) == 3
        for result, hit, _ in results:
            assert hit is True
            assert result == "shared content"
    
    def test_get_or_wait_timeout(self):
        """get_or_wait 超时返回未命中"""
        import threading
        import time
        
        cache = ToolCache()
        
        # 首次请求注册 pending
        result1, hit1 = cache.get_or_wait("read_file", {"path": "/test.txt"}, timeout=0.1)
        assert hit1 is False
        
        # 第二次请求应等待，但超时返回
        start = time.time()
        result2, hit2 = cache.get_or_wait("read_file", {"path": "/test.txt"}, timeout=0.1)
        elapsed = time.time() - start
        
        assert hit2 is False
        assert result2 is None
        assert elapsed >= 0.1  # 应等待至少 0.1 秒
    
    def test_mark_done_notifies_waiters(self):
        """mark_done 通知等待线程"""
        import threading
        import time
        
        cache = ToolCache()
        results = []
        
        def waiter():
            result, hit = cache.get_or_wait("read_file", {"path": "/test.txt"}, timeout=5.0)
            results.append((result, hit))
        
        # 首先调用 get_or_wait 注册 pending（模拟首次请求）
        result0, hit0 = cache.get_or_wait("read_file", {"path": "/test.txt"}, timeout=1.0)
        assert hit0 is False  # 首次应返回未命中
        
        # 启动等待线程（第二次请求会等待）
        t = threading.Thread(target=waiter)
        t.start()
        
        # 等待线程进入等待状态
        time.sleep(0.1)
        
        # 通知等待线程
        cache.mark_done("read_file", {"path": "/test.txt"}, "done content")
        
        t.join(timeout=1.0)
        
        assert len(results) == 1
        assert results[0] == ("done content", True)
