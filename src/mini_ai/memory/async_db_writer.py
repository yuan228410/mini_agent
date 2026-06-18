"""异步数据库写入器 - 批量写入优化与读取一致性保证

核心功能：
1. 单一后台线程处理所有写入
2. 批量写入优化（时间窗口 + 数量阈值）
3. 读取一致性保证（预读缓存）
4. 持久化保证（atexit + 信号处理）
"""
import atexit
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Callable, Optional

from ..logger import logger
from ..utils import _UTC8


@dataclass
class WriteTask:
    """写入任务"""
    workspace: str
    session_id: str
    role: str
    content: str
    metadata: str
    ts: str
    task_id: int = 0  # 用于跟踪任务
    callback: Optional[Callable[[int], None]] = None  # 写入完成回调


@dataclass
class FlushTask:
    """刷盘任务（标记）"""
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None


@dataclass
class StopTask:
    """停止任务（标记）"""
    pass


class AsyncDBWriter:
    """异步数据库写入器
    
    核心功能：
    - 单一后台线程处理所有写入
    - 批量写入优化（时间窗口 + 数量阈值）
    - 读取一致性保证（预读缓存）
    - 持久化保证（atexit + 信号处理）
    
    使用方式：
        writer = AsyncDBWriter(db_path)
        writer.start()
        
        # 异步写入
        writer.submit_write(workspace, session_id, role, content, metadata)
        
        # 同步读取（自动处理缓存一致性）
        messages = writer.load_session(workspace, session_id)
        
        # 停止
        writer.stop()
    """
    
    # 批量写入配置
    BATCH_TIME_WINDOW = 0.1  # 100ms 时间窗口
    BATCH_SIZE_THRESHOLD = 50  # 50 条数量阈值
    MAX_RETRY_COUNT = 3  # 最大重试次数
    QUEUE_MAX_SIZE = 10000  # 队列最大容量
    
    def __init__(
        self,
        db_path: Path,
        conn_factory: Optional[Callable[[], sqlite3.Connection]] = None,
        *,
        batch_time_window: float | None = None,
        batch_size_threshold: int | None = None,
        max_retry_count: int | None = None,
        queue_max_size: int | None = None,
        submit_timeout: float = 1.0,
        on_full: str = "block",
    ):
        """初始化异步写入器

        Args:
            db_path: 数据库路径
            conn_factory: 数据库连接工厂（用于测试）
            batch_time_window: 批量写入时间窗口
            batch_size_threshold: 批量写入数量阈值
            max_retry_count: 最大重试次数
            queue_max_size: 队列最大容量
            submit_timeout: 队列满时等待时间
            on_full: 队列满策略，block/fail/sync_write
        """
        self.db_path = Path(db_path)
        self._conn_factory = conn_factory
        self._conn: Optional[sqlite3.Connection] = None
        self.BATCH_TIME_WINDOW = batch_time_window if batch_time_window is not None else self.BATCH_TIME_WINDOW
        self.BATCH_SIZE_THRESHOLD = batch_size_threshold if batch_size_threshold is not None else self.BATCH_SIZE_THRESHOLD
        self.MAX_RETRY_COUNT = max_retry_count if max_retry_count is not None else self.MAX_RETRY_COUNT
        self.QUEUE_MAX_SIZE = queue_max_size if queue_max_size is not None else self.QUEUE_MAX_SIZE
        self.SUBMIT_TIMEOUT = submit_timeout
        self.ON_FULL = on_full

        # 写入队列
        self._queue: Queue = Queue(maxsize=self.QUEUE_MAX_SIZE)
        
        # 后台线程
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False
        
        # 任务计数器（用于跟踪未完成任务）
        self._task_counter = 0
        self._task_counter_lock = threading.Lock()
        
        # 读取一致性：预读缓存
        # 结构：{(workspace, session_id): [messages]}
        self._cache: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._cache_lock = threading.Lock()
        self._cache_max_size = 10000  # 每个会话最多缓存消息数
        
        # 统计信息
        self._stats = {
            "total_writes": 0,
            "batch_writes": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "queue_full_warnings": 0,
            "write_errors": 0,
            "sync_fallback_writes": 0,
            "queue_full_failures": 0,
            "flush_timeouts": 0,
        }
        self._stats_lock = threading.Lock()
        
        # 注册 atexit（信号处理由宿主进程负责，避免覆盖 uvicorn 等框架的处理器）
        atexit.register(self._emergency_flush)
        
        logger.debug(f"[AsyncDBWriter] 初始化: path={db_path}")
    
    def _emergency_flush(self):
        """紧急刷盘（程序退出时调用）"""
        if not self._started:
            return

        logger.info("[AsyncDBWriter] 执行紧急刷盘...")
        try:
            self.stop(timeout=5.0)
        except Exception as e:
            logger.warning(f"[AsyncDBWriter] 正常停止失败，尝试保底刷盘: {e}")
            self._stop_event.set()
            self._flush_remaining_tasks()
            self._close_connection()

        logger.info("[AsyncDBWriter] 紧急刷盘完成")
    
    def _flush_remaining_tasks(self):
        """刷盘剩余任务（最后保底）"""
        tasks = []
        while not self._queue.empty():
            try:
                task = self._queue.get_nowait()
                if isinstance(task, WriteTask):
                    tasks.append(task)
            except Empty:
                break
        
        if tasks:
            logger.warning(f"[AsyncDBWriter] 紧急写入 {len(tasks)} 条剩余消息")
            self._write_batch(tasks)
    
    def start(self):
        """启动后台写入线程"""
        if self._started:
            logger.warning("[AsyncDBWriter] 已经启动，跳过")
            return
        
        self._started = True
        self._stop_event.clear()
        
        # 初始化数据库连接
        self._init_connection()
        
        # 启动后台线程
        self._thread = threading.Thread(
            target=self._write_loop,
            name="AsyncDBWriter",
            daemon=True,
        )
        self._thread.start()
        
        logger.info("[AsyncDBWriter] 后台线程已启动")
    
    def stop(self, timeout: float = 5.0):
        """停止后台写入线程

        Args:
            timeout: 等待超时时间（秒）
        """
        if not self._started:
            return

        logger.info("[AsyncDBWriter] 停止中...")

        # 先可靠刷盘，再发送停止信号
        try:
            self.flush(timeout=timeout)
        except Exception as e:
            logger.warning(f"[AsyncDBWriter] 停止前 flush 失败: {e}")
        self._stop_event.set()
        try:
            self._queue.put(StopTask(), timeout=min(timeout, 1.0))
        except Full:
            logger.warning("[AsyncDBWriter] 停止信号入队超时")

        # 等待线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("[AsyncDBWriter] 线程未能在超时内停止")

        # 关闭数据库连接
        self._close_connection()

        self._started = False
        logger.info(f"[AsyncDBWriter] 已停止，统计: {self._stats}")
    
    def _init_connection(self):
        """初始化数据库连接"""
        if self._conn_factory:
            self._conn = self._conn_factory()
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            # 启用 WAL 模式
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
    
    def _close_connection(self):
        """关闭数据库连接"""
        if self._conn:
            try:
                self._conn.close()
            except Exception as e:
                logger.warning(f"[AsyncDBWriter] 关闭连接失败: {e}")
            finally:
                self._conn = None

    def _enqueue_write_task(self, task: WriteTask):
        """按配置策略提交写入任务。"""
        try:
            self._queue.put_nowait(task)
            return
        except Full:
            with self._stats_lock:
                self._stats["queue_full_warnings"] += 1
            logger.warning(f"[AsyncDBWriter] 队列已满（{self._queue.qsize()}），策略={self.ON_FULL}")

        if self.ON_FULL == "fail":
            with self._stats_lock:
                self._stats["queue_full_failures"] += 1
            raise Full("AsyncDBWriter queue is full")

        if self.ON_FULL == "sync_write":
            with self._stats_lock:
                self._stats["sync_fallback_writes"] += 1
            self._write_batch([task])
            return

        try:
            self._queue.put(task, timeout=self.SUBMIT_TIMEOUT)
        except Full:
            with self._stats_lock:
                self._stats["queue_full_failures"] += 1
            raise

    def submit_write(self, workspace: str, session_id: str, role: str,
                     content: str, metadata: str = "", 
                     callback: Optional[Callable[[int], None]] = None) -> int:
        """提交写入任务
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            role: 角色
            content: 消息内容
            metadata: 扩展元数据
            callback: 写入完成回调（参数为消息ID）
        
        Returns:
            任务ID（用于跟踪）
        """
        if not self._started:
            logger.warning("[AsyncDBWriter] 未启动，自动启动")
            self.start()
        
        # 生成任务ID
        with self._task_counter_lock:
            self._task_counter += 1
            task_id = self._task_counter
        
        # 创建任务
        task = WriteTask(
            workspace=workspace,
            session_id=session_id,
            role=role,
            content=content,
            metadata=metadata,
            ts=datetime.now(_UTC8).isoformat(),
            task_id=task_id,
            callback=callback,
        )
        
        # 立即更新缓存（读取一致性保证）
        self._add_to_cache(workspace, session_id, {
            "role": role,
            "content": content,
            "metadata": metadata,
            "timestamp": task.ts[:19],
            "_task_id": task_id,
        })
        
        # 加入队列
        self._enqueue_write_task(task)

        return task_id
    
    def submit_batch(self, workspace: str, session_id: str, 
                     messages: list[dict]) -> int:
        """批量提交写入任务
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            messages: 消息列表
        
        Returns:
            提交的任务数量
        """
        if not messages:
            return 0
        
        count = 0
        ts = datetime.now(_UTC8).isoformat()
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            metadata = msg.get("metadata", "")
            with self._task_counter_lock:
                self._task_counter += 1
                task_id = self._task_counter

            # 创建任务
            task = WriteTask(
                workspace=workspace,
                session_id=session_id,
                role=role,
                content=content,
                metadata=metadata,
                ts=ts,
                task_id=task_id,
            )

            # 更新缓存
            self._add_to_cache(workspace, session_id, {
                "role": role,
                "content": content,
                "metadata": metadata,
                "timestamp": ts[:19],
                "_task_id": task_id,
            })
            
            self._enqueue_write_task(task)
            count += 1
        
        return count
    
    def flush(self, timeout: float = 5.0):
        """等待 flush 任务之前的所有写入真实落库。

        Args:
            timeout: 超时时间
        """
        if not self._started:
            return

        original_size = self._queue.qsize()
        flush_task = FlushTask()
        try:
            self._queue.put(flush_task, timeout=timeout)
        except Full as e:
            with self._stats_lock:
                self._stats["flush_timeouts"] += 1
            raise TimeoutError("提交 flush 任务超时") from e

        if not flush_task.done.wait(timeout=timeout):
            with self._stats_lock:
                self._stats["flush_timeouts"] += 1
            raise TimeoutError("等待 flush 完成超时")
        if flush_task.error:
            raise flush_task.error

        logger.debug(f"[AsyncDBWriter] flush 完成，处理了 {original_size} 个排队任务")
    
    def _write_loop(self):
        """后台写入循环"""
        logger.debug("[AsyncDBWriter] 写入线程启动")

        batch = []
        last_flush_time = time.time()

        while True:
            task = None
            try:
                # 尝试获取任务
                try:
                    task = self._queue.get(timeout=0.01)
                except Empty:
                    # 队列为空，检查是否需要刷盘
                    if batch and (time.time() - last_flush_time) >= self.BATCH_TIME_WINDOW:
                        self._write_batch(batch)
                        batch = []
                        last_flush_time = time.time()
                    if self._stop_event.is_set():
                        break
                    continue

                # 处理特殊任务
                if isinstance(task, StopTask):
                    logger.debug("[AsyncDBWriter] 收到停止信号")
                    break
                elif isinstance(task, FlushTask):
                    try:
                        if batch:
                            self._write_batch(batch)
                            batch = []
                        last_flush_time = time.time()
                    except Exception as e:
                        task.error = e
                        logger.error(f"[AsyncDBWriter] flush 失败: {e}", exc_info=True)
                    finally:
                        task.done.set()
                    continue

                # 添加到批处理
                batch.append(task)

                # 检查是否达到批量阈值
                if len(batch) >= self.BATCH_SIZE_THRESHOLD:
                    self._write_batch(batch)
                    batch = []
                    last_flush_time = time.time()
                elif time.time() - last_flush_time >= self.BATCH_TIME_WINDOW:
                    # 时间窗口到期
                    self._write_batch(batch)
                    batch = []
                    last_flush_time = time.time()

            except Exception as e:
                logger.error(f"[AsyncDBWriter] 写入循环异常: {e}", exc_info=True)
            finally:
                if task is not None:
                    self._queue.task_done()

        # 退出前处理剩余任务（包括队列中的）
        remaining_tasks = []
        while not self._queue.empty():
            task = None
            try:
                task = self._queue.get_nowait()
                if isinstance(task, FlushTask):
                    task.error = RuntimeError("AsyncDBWriter stopped before flush completed")
                    task.done.set()
                elif not isinstance(task, StopTask):
                    remaining_tasks.append(task)
            except Empty:
                break
            finally:
                if task is not None:
                    self._queue.task_done()

        if batch or remaining_tasks:
            all_remaining = batch + remaining_tasks
            logger.info(f"[AsyncDBWriter] 退出前写入 {len(all_remaining)} 条剩余消息")
            self._write_batch(all_remaining)

        logger.debug("[AsyncDBWriter] 写入线程退出")
    
    def _write_batch(self, tasks: list[WriteTask]):
        """批量写入任务
        
        Args:
            tasks: 写入任务列表
        """
        if not tasks:
            return
        
        for retry in range(self.MAX_RETRY_COUNT):
            try:
                self._do_write_batch(tasks)
                
                # 写入成功后仅清理本批任务，避免误删同会话新提交但尚未落库的缓存
                written_ids: dict[tuple[str, str], set[int]] = defaultdict(set)
                for task in tasks:
                    written_ids[(task.workspace, task.session_id)].add(task.task_id)
                with self._cache_lock:
                    for key, task_ids in written_ids.items():
                        if key in self._cache:
                            self._cache[key] = [m for m in self._cache[key] if m.get("_task_id") not in task_ids]
                            if not self._cache[key]:
                                del self._cache[key]
                
                # 更新统计
                with self._stats_lock:
                    self._stats["total_writes"] += len(tasks)
                    self._stats["batch_writes"] += 1
                
                logger.debug(f"[AsyncDBWriter] 批量写入成功: {len(tasks)} 条")
                return
                
            except Exception as e:
                with self._stats_lock:
                    self._stats["write_errors"] += 1
                
                if retry < self.MAX_RETRY_COUNT - 1:
                    logger.warning(f"[AsyncDBWriter] 写入失败，重试 {retry + 1}/{self.MAX_RETRY_COUNT}: {e}")
                    time.sleep(0.1 * (retry + 1))  # 指数退避
                else:
                    logger.error(f"[AsyncDBWriter] 写入失败，已重试 {self.MAX_RETRY_COUNT} 次: {e}")
                    # 写入失败，从缓存中移除
                    for task in tasks:
                        self._remove_from_cache(task.workspace, task.session_id, task.content)
    
    def _do_write_batch(self, tasks: list[WriteTask]):
        """执行批量写入（事务保护）
        
        Args:
            tasks: 写入任务列表
        """
        if not self._conn:
            self._init_connection()
        
        try:
            self._conn.execute("BEGIN")
            
            for task in tasks:
                cur = self._conn.execute(
                    "INSERT INTO messages (workspace, session_id, ts, role, content, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                    (task.workspace, task.session_id, task.ts, task.role, task.content, task.metadata),
                )
                msg_id = cur.lastrowid
                
                # 写入 FTS 索引
                try:
                    self._conn.execute(
                        "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                        (msg_id, task.content),
                    )
                except sqlite3.OperationalError:
                    pass  # FTS 不可用，忽略
                
                # 调用回调
                if task.callback:
                    try:
                        task.callback(msg_id)
                    except Exception as e:
                        logger.warning(f"[AsyncDBWriter] 回调失败: {e}")
            
            self._conn.commit()
            
        except Exception as e:
            self._conn.rollback()
            raise
    
    # === 缓存管理 ===
    
    def _add_to_cache(self, workspace: str, session_id: str, msg: dict):
        """添加消息到缓存
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            msg: 消息字典
        """
        with self._cache_lock:
            key = (workspace, session_id)
            cache = self._cache[key]
            cache.append(msg)
            
            # 限制缓存大小
            if len(cache) > self._cache_max_size:
                # 保留最新的消息
                self._cache[key] = cache[-self._cache_max_size:]
    
    def _remove_from_cache(self, workspace: str, session_id: str, content: str):
        """从缓存中移除消息（写入失败时调用）
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            content: 消息内容
        """
        with self._cache_lock:
            key = (workspace, session_id)
            cache = self._cache[key]
            # 移除最后一条匹配的消息
            for i in range(len(cache) - 1, -1, -1):
                if cache[i].get("content") == content:
                    cache.pop(i)
                    break
    
    def get_cached_messages(self, workspace: str, session_id: str) -> list[dict]:
        """获取缓存的消息
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
        
        Returns:
            缓存的消息列表
        """
        with self._cache_lock:
            key = (workspace, session_id)
            return list(self._cache.get(key, []))
    
    def clear_cache(self, workspace: str = "", session_id: str = ""):
        """清空缓存
        
        Args:
            workspace: 工作空间名称（空则清空所有）
            session_id: 会话ID（空则清空指定工作空间的所有缓存）
        """
        with self._cache_lock:
            if not workspace:
                self._cache.clear()
            elif not session_id:
                keys_to_remove = [k for k in self._cache.keys() if k[0] == workspace]
                for k in keys_to_remove:
                    del self._cache[k]
            else:
                key = (workspace, session_id)
                if key in self._cache:
                    del self._cache[key]
    
    # === 读取操作（一致性保证）===
    
    def load_session_with_cache(self, workspace: str, session_id: str,
                                 db_loader: Callable[[str, str], list[dict]],
                                 limit: int = 0) -> list[dict]:
        """加载会话消息（缓存 + 数据库）
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            db_loader: 数据库加载函数
            limit: 限制数量
        
        Returns:
            消息列表
        """
        # 先从数据库加载
        db_messages = db_loader(workspace, session_id, limit=0)
        
        # 获取缓存的消息
        cached_messages = self.get_cached_messages(workspace, session_id)
        
        if not cached_messages:
            with self._stats_lock:
                self._stats["cache_misses"] += 1
            if limit > 0:
                return db_messages[-limit:] if len(db_messages) > limit else db_messages
            return db_messages
        
        with self._stats_lock:
            self._stats["cache_hits"] += 1
        
        # 合并消息（缓存的消息追加到数据库消息后面），按稳定字段去重
        seen = {
            (m.get("role"), m.get("content"), m.get("metadata", ""), m.get("timestamp") or m.get("ts"))
            for m in db_messages
        }
        cache_to_add = []
        for msg in cached_messages:
            key = (msg.get("role"), msg.get("content"), msg.get("metadata", ""), msg.get("timestamp") or msg.get("ts"))
            if key in seen:
                continue
            clean_msg = {k: v for k, v in msg.items() if k != "_task_id"}
            cache_to_add.append(clean_msg)
            seen.add(key)
        all_messages = db_messages + cache_to_add
        
        if limit > 0 and len(all_messages) > limit:
            return all_messages[-limit:]
        
        return all_messages
    
    # === 统计信息 ===
    
    def get_stats(self) -> dict:
        """获取统计信息
        
        Returns:
            统计字典
        """
        with self._stats_lock:
            stats = dict(self._stats)
        
        stats["queue_size"] = self._queue.qsize()
        stats["cache_size"] = len(self._cache)
        stats["is_running"] = self._started
        
        return stats
    
    def get_queue_size(self) -> int:
        """获取队列大小
        
        Returns:
            队列中的任务数量
        """
        return self._queue.qsize()
    
    def is_running(self) -> bool:
        """检查是否正在运行
        
        Returns:
            是否正在运行
        """
        return self._started and self._thread is not None and self._thread.is_alive()
