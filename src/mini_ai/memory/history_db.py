"""历史消息 SQLite 存储 — 统一数据库 + FTS5 全文搜索"""
import atexit
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..utils import _UTC8
from ..logger import logger
from ..core.messages import ChatMessage
from .async_db_writer import AsyncDBWriter


def _process_multimodal_content(content: str | list, metadata: str = "") -> tuple[str, str]:
    """处理多模态消息内容，提取文本并保存完整结构到 metadata。

    Args:
        content: 消息内容，可能是字符串或列表（多模态）
        metadata: 原有 metadata JSON 字符串

    Returns:
        (text_content, updated_metadata): 处理后的文本内容和更新后的 metadata
    """
    if not isinstance(content, list):
        return content or "", metadata

    # 提取文本内容用于搜索
    text_parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            text_parts.append(part.get("text", ""))
    text_content = "\n".join(text_parts)

    # 保存完整结构到 metadata
    try:
        meta_dict = json.loads(metadata) if metadata else {}
        meta_dict["_multimodal_content"] = content
        metadata = json.dumps(meta_dict, ensure_ascii=False)
    except Exception:
        pass

    return text_content, metadata


def _metadata_to_dict(metadata: str | dict | None) -> dict:
    if isinstance(metadata, dict):
        return dict(metadata)
    if not metadata:
        return {}
    try:
        parsed = json.loads(metadata)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _metadata_to_json(metadata: str | dict | None) -> str:
    if isinstance(metadata, str):
        return metadata
    if isinstance(metadata, dict) and metadata:
        return json.dumps(metadata, ensure_ascii=False)
    return ""


def _history_row_from_message(message: ChatMessage | dict) -> dict:
    """Normalize a runtime/history message through ChatMessage before DB writes."""
    chat_msg = message if isinstance(message, ChatMessage) else ChatMessage.from_dict(message)
    raw = chat_msg.to_dict(include_internal=True, include_tool_results=True)
    metadata = _metadata_to_dict(raw.get("metadata"))
    known = {"role", "content", "timestamp", "metadata"}
    for key, value in raw.items():
        if key not in known and value is not None:
            metadata[key] = value
    return {
        "role": chat_msg.role.value,
        "content": raw.get("content", ""),
        "metadata": _metadata_to_json(metadata),
    }


def _message_from_history_row(role: str, content, metadata: str = "", timestamp: str = "") -> dict:
    """Rehydrate DB row metadata through ChatMessage for a stable runtime shape."""
    meta = _metadata_to_dict(metadata)
    if "_multimodal_content" in meta:
        content = meta.pop("_multimodal_content")
    payload = {"role": role, "content": content}
    if timestamp:
        payload["timestamp"] = timestamp[:19]
    payload.update({k: v for k, v in meta.items() if k not in ("role", "content")})
    return ChatMessage.from_dict(payload).to_dict(include_internal=True, include_tool_results=True)


class HistoryDB:
    """统一历史数据库
    
    数据库路径：~/.mini_ai/users/<username>/history.db
    每个用户一个数据库文件，所有工作空间、所有会话共享。
    
    支持同步/异步写入模式：
    - 同步模式（默认）：直接写入数据库，保证数据持久化
    - 异步模式：使用后台线程批量写入，提升性能
    
    异步模式特性：
    - 批量写入优化（100ms 时间窗口 + 50 条数量阈值）
    - 读取一致性保证（预读缓存）
    - 持久化保证（atexit + 信号处理）
    """
    
    def __init__(self, db_path: Path, async_write: bool | None = None):
        """初始化数据库
        
        Args:
            db_path: 数据库路径
            async_write: 是否启用异步写入模式
                         None: 从配置读取（Web 端默认 true，CLI 端默认 false）
                         True: 强制启用异步写入
                         False: 强制使用同步写入
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # P0#4: RLock 防止 search_fts→search 递归死锁
        self._fts_available = True
        self._closed = False
        
        # 确定是否启用异步写入
        if async_write is None:
            # 从配置读取
            from ..config import DATABASE
            async_write = DATABASE.get("history", {}).get("async_write", None)
            # 如果配置也是 None，默认关闭（CLI 模式）
            if async_write is None:
                async_write = False
        
        self._async_write = async_write
        self._async_writer: Optional[AsyncDBWriter] = None
        
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        # 启用 WAL 模式和多线程优化
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")  # 5秒超时
        self._conn.execute("PRAGMA synchronous=NORMAL")  # 平衡性能和安全
        self._init_schema()
        
        # 初始化异步写入器
        if self._async_write:
            from ..config import DATABASE
            db_config = DATABASE.get("history", {})
            self._async_writer = AsyncDBWriter(
                self.db_path,
                batch_size_threshold=db_config.get("batch_size"),
                batch_time_window=db_config.get("batch_timeout"),
                queue_max_size=db_config.get("queue_size"),
                max_retry_count=db_config.get("retry_count"),
                submit_timeout=db_config.get("submit_timeout", 1.0),
                on_full=db_config.get("on_full", "block"),
            )

            self._async_writer.start()
        
        atexit.register(self.close)
        logger.debug(f"[HistoryDB] 初始化: path={db_path}, WAL模式已启用, async_write={async_write}")
    
    def _init_schema(self):
        """初始化数据库表结构"""
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    metadata TEXT DEFAULT ''
                );
                
                -- 索引优化
                CREATE INDEX IF NOT EXISTS idx_workspace ON messages(workspace);
                CREATE INDEX IF NOT EXISTS idx_session ON messages(workspace, session_id);
                CREATE INDEX IF NOT EXISTS idx_ts ON messages(ts);
                CREATE INDEX IF NOT EXISTS idx_role ON messages(role);

                CREATE TABLE IF NOT EXISTS plan_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace, session_id, plan_id, revision)
                );
                CREATE INDEX IF NOT EXISTS idx_plan_current ON plan_artifacts(workspace, session_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_plan_status ON plan_artifacts(workspace, session_id, status);
            """)
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content=messages, content_rowid=id, tokenize='unicode61')"
                )
            except sqlite3.OperationalError:
                self._fts_available = False
                logger.warning("[HistoryDB] FTS5 不可用，全文搜索将退化为模糊匹配")
    
    def is_fts_available(self) -> bool:
        """返回 FTS5 是否可用，供上层判断搜索模式"""
        return self._fts_available
    
    def _ensure_conn(self):
        """确保连接可用，已关闭则重新创建"""
        if self._closed:
            # 关闭旧连接（如果存在）
            if hasattr(self, '_conn') and self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
            self._closed = False
            logger.debug(f"[HistoryDB] 重新建立连接: {self.db_path}")
            return
        try:
            self._conn.execute("SELECT 1")
        except Exception:
            # 关闭旧连接
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
    
    # === 写入操作 ===
    
    def append(self, workspace: str, session_id: str, role: str, 
               content: str | list, metadata: str = "") -> int:
        """写入单条消息
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            role: 角色 (system/user/assistant/tool)
            content: 消息内容
            metadata: 扩展元数据 (JSON)
        
        Returns:
            插入的消息ID（异步模式下返回任务ID）
        """
        content, metadata = _process_multimodal_content(content, metadata)

        ts = datetime.now(_UTC8).isoformat()
        content_preview = (content or "")[:80].replace("\n", " ")
        logger.debug(f"[HistoryDB] append: workspace={workspace}, sid={session_id}, role={role}, len={len(content or '')}, preview={content_preview}")
        
        # 异步写入模式
        if self._async_write and self._async_writer:
            return self._async_writer.submit_write(workspace, session_id, role, content, metadata)
        
        # 同步写入模式
        with self._lock:
            self._ensure_conn()
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO messages (workspace, session_id, ts, role, content, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                    (workspace, session_id, ts, role, content, metadata),
                )
                msg_id = cur.lastrowid
                try:
                    self._conn.execute(
                        "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                        (msg_id, content),
                    )
                except sqlite3.OperationalError:
                    if self._fts_available:
                        self._fts_available = False
                        logger.warning("[HistoryDB] FTS5 写入失败，后续搜索将退化为模糊匹配")
        
        return msg_id
    
    def append_batch(self, workspace: str, session_id: str, 
                     messages: list[dict]) -> int:
        """批量写入消息（事务保护）
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            messages: 消息列表，每条消息包含 role, content, metadata
        
        Returns:
            插入的消息数量
        """
        if not messages:
            return 0

        normalized_messages = [_history_row_from_message(msg) for msg in messages]
        ts = datetime.now(_UTC8).isoformat()

        # 异步写入模式
        if self._async_write and self._async_writer:
            return self._async_writer.submit_batch(workspace, session_id, normalized_messages)

        # 同步写入模式
        count = 0
        
        with self._lock:
            self._ensure_conn()
            try:
                self._conn.execute("BEGIN")
                for msg in normalized_messages:
                    row = _history_row_from_message(msg)
                    role = row["role"]
                    content = row.get("content", "")
                    metadata = row.get("metadata", "")

                    content, metadata = _process_multimodal_content(content, metadata)

                    cur = self._conn.execute(
                        "INSERT INTO messages (workspace, session_id, ts, role, content, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                        (workspace, session_id, ts, role, content, metadata),
                    )
                    try:
                        self._conn.execute(
                            "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                            (cur.lastrowid, content),
                        )
                    except sqlite3.OperationalError:
                        if self._fts_available:
                            self._fts_available = False
                    count += 1
                
                self._conn.commit()
                logger.debug(f"[HistoryDB] append_batch: 插入 {count} 条消息, workspace={workspace}, sid={session_id}")
            except Exception as e:
                self._conn.rollback()
                logger.error(f"[HistoryDB] append_batch 失败: {e}")
                raise
        
        return count
    
    # === 读取操作 ===
    
    def load_session(self, workspace: str, session_id: str, 
                     limit: int = 0) -> list[dict]:
        """加载指定会话的所有消息
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            limit: 限制数量（0表示不限制）
        
        Returns:
            消息列表
        """
        logger.debug(f"[HistoryDB] load_session: workspace={workspace}, sid={session_id}, limit={limit}")
        
        # 异步写入模式：合并缓存和数据库消息
        if self._async_write and self._async_writer:
            return self._async_writer.load_session_with_cache(
                workspace, session_id,
                self._load_session_from_db,
                limit
            )
        
        # 同步模式：直接从数据库加载
        return self._load_session_from_db(workspace, session_id, limit)
    
    def _load_session_from_db(self, workspace: str, session_id: str, 
                               limit: int = 0) -> list[dict]:
        """从数据库加载会话消息（内部方法）
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            limit: 限制数量
        
        Returns:
            消息列表
        """
        with self._lock:
            self._ensure_conn()
            if limit > 0:
                rows = self._conn.execute(
                    "SELECT role, content, metadata, ts FROM (SELECT id, role, content, metadata, ts FROM messages WHERE workspace=? AND session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                    (workspace, session_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT role, content, metadata, ts FROM messages WHERE workspace=? AND session_id=? ORDER BY id",
                    (workspace, session_id),
                ).fetchall()
        
        return self._parse_messages(rows)
    
    def load_session_for_display(self, workspace: str, session_id: str,
                                  limit: int = 0) -> list[dict]:
        """加载会话消息供前端显示 — 只返回 user/assistant 消息。"""
        messages = self.load_session(workspace, session_id, limit=0)
        display_messages = [m for m in messages if m.get("role") in ("user", "assistant")]
        if limit > 0 and len(display_messages) > limit:
            return display_messages[-limit:]
        return display_messages
    
    def load_recent(self, workspace: str = "", limit: int = 100) -> list[dict]:
        """加载最近消息（跨会话）
        
        Args:
            workspace: 工作空间名称（空则加载所有）
            limit: 限制数量
        
        Returns:
            消息列表（含 workspace, session_id 字段）
        """
        with self._lock:
            self._ensure_conn()
            if workspace:
                rows = self._conn.execute(
                    "SELECT workspace, session_id, role, content, metadata, ts FROM messages WHERE workspace=? ORDER BY id DESC LIMIT ?",
                    (workspace, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT workspace, session_id, role, content, metadata, ts FROM messages ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        
        results = []
        for workspace, session_id, role, content, metadata, ts in rows:
            msg = _message_from_history_row(role, content, metadata, ts)
            msg["workspace"] = workspace
            msg["session_id"] = session_id
            results.append(msg)

        return results
    
    # === 搜索操作 ===
    
    def search(self, keyword: str = "", workspace: str = "", session_id: str = "",
               date_from: str = "", date_to: str = "", limit: int = 20) -> list[dict]:
        """搜索历史消息
        
        Args:
            keyword: 搜索关键词
            workspace: 工作空间（空则搜索所有）
            session_id: 会话ID（空则搜索所有）
            date_from: 起始日期
            date_to: 结束日期
            limit: 返回数量限制
        
        Returns:
            消息列表
        """
        logger.debug(f"[HistoryDB] search: keyword={keyword[:30] if keyword else '(空)'}, workspace={workspace}, sid={session_id}, limit={limit}")
        
        conditions = []
        params = []
        
        if workspace:
            conditions.append("workspace=?")
            params.append(workspace)
        if session_id:
            conditions.append("session_id=?")
            params.append(session_id)
        if date_from:
            conditions.append("ts >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("ts <= ?")
            params.append(date_to)
        if keyword:
            conditions.append("content LIKE ?")
            params.append(f"%{keyword}%")
        
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        
        with self._lock:
            self._ensure_conn()
            rows = self._conn.execute(
                f"SELECT id, workspace, session_id, ts, role, content FROM messages "
                f"{where_clause} ORDER BY ts DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        
        return [
            {
                "id": id,
                "workspace": ws,
                "session_id": sid,
                "ts": ts,
                "role": role,
                "content": content
            }
            for id, ws, sid, ts, role, content in rows
        ]
    
    def search_fts(self, keyword: str, workspace: str = "", 
                   limit: int = 20) -> list[dict]:
        """全文搜索（FTS5）
        
        Args:
            keyword: 搜索关键词
            workspace: 工作空间（空则搜索所有）
            limit: 返回数量限制
        
        Returns:
            消息列表
        """
        if not self._fts_available:
            return self.search(keyword=keyword, workspace=workspace, limit=limit)
        
        with self._lock:
            self._ensure_conn()
            try:
                if workspace:
                    rows = self._conn.execute(
                        """SELECT m.id, m.workspace, m.session_id, m.ts, m.role, m.content
                           FROM messages m
                           JOIN messages_fts fts ON m.id = fts.rowid
                           WHERE messages_fts MATCH ? AND m.workspace = ?
                           ORDER BY m.ts DESC LIMIT ?""",
                        (keyword, workspace, limit),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        """SELECT m.id, m.workspace, m.session_id, m.ts, m.role, m.content
                           FROM messages m
                           JOIN messages_fts fts ON m.id = fts.rowid
                           WHERE messages_fts MATCH ?
                           ORDER BY m.ts DESC LIMIT ?""",
                        (keyword, limit),
                    ).fetchall()
                
                return [
                    {
                        "id": id,
                        "workspace": ws,
                        "session_id": sid,
                        "ts": ts,
                        "role": role,
                        "content": content
                    }
                    for id, ws, sid, ts, role, content in rows
                ]
            except sqlite3.OperationalError:
                # FTS 查询失败，退化为 LIKE 搜索
                return self.search(keyword=keyword, workspace=workspace, limit=limit)
    
    # === 计划产物操作 ===

    def save_plan(self, workspace: str, session_id: str, artifact: dict) -> None:
        """保存结构化计划产物。"""
        payload = json.dumps(artifact, ensure_ascii=False)
        created_at = artifact.get("created_at") or datetime.now(_UTC8).isoformat()
        updated_at = artifact.get("updated_at") or datetime.now(_UTC8).isoformat()
        with self._lock:
            self._ensure_conn()
            with self._conn:
                self._conn.execute(
                    """INSERT OR REPLACE INTO plan_artifacts
                       (workspace, session_id, plan_id, revision, status, artifact_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        workspace,
                        session_id,
                        artifact.get("plan_id", ""),
                        int(artifact.get("revision") or 0),
                        artifact.get("status", ""),
                        payload,
                        created_at,
                        updated_at,
                    ),
                )

    def get_current_plan(self, workspace: str, session_id: str) -> dict | None:
        """返回当前会话最新的非终态计划。"""
        with self._lock:
            self._ensure_conn()
            row = self._conn.execute(
                """SELECT artifact_json FROM plan_artifacts
                   WHERE workspace=? AND session_id=? AND status NOT IN ('completed', 'cancelled', 'superseded')
                   ORDER BY updated_at DESC, revision DESC LIMIT 1""",
                (workspace, session_id),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def list_plans(self, workspace: str, session_id: str) -> list[dict]:
        with self._lock:
            self._ensure_conn()
            rows = self._conn.execute(
                """SELECT artifact_json FROM plan_artifacts
                   WHERE workspace=? AND session_id=?
                   ORDER BY updated_at DESC, revision DESC""",
                (workspace, session_id),
            ).fetchall()
        plans: list[dict] = []
        for row in rows:
            try:
                plans.append(json.loads(row[0]))
            except Exception:
                pass
        return plans

    def mark_plan_status(self, workspace: str, session_id: str, plan_id: str, status: str) -> None:
        with self._lock:
            self._ensure_conn()
            with self._conn:
                rows = self._conn.execute(
                    """SELECT id, artifact_json FROM plan_artifacts
                       WHERE workspace=? AND session_id=? AND plan_id=?""",
                    (workspace, session_id, plan_id),
                ).fetchall()
                for row_id, payload in rows:
                    try:
                        artifact = json.loads(payload)
                        artifact["status"] = status
                        artifact["updated_at"] = datetime.now(_UTC8).isoformat()
                        payload = json.dumps(artifact, ensure_ascii=False)
                    except Exception:
                        pass
                    self._conn.execute(
                        "UPDATE plan_artifacts SET status=?, artifact_json=?, updated_at=? WHERE id=?",
                        (status, payload, datetime.now(_UTC8).isoformat(), row_id),
                    )

    # === 删除操作 ===

    def delete_session(self, workspace: str, session_id: str) -> int:
        """删除指定会话的所有消息
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
        
        Returns:
            删除的消息数量
        """
        with self._lock:
            self._ensure_conn()
            with self._conn:
                try:
                    self._conn.execute(
                        "DELETE FROM messages_fts WHERE rowid IN (SELECT id FROM messages WHERE workspace=? AND session_id=?)",
                        (workspace, session_id),
                    )
                except Exception:
                    pass
                self._conn.execute(
                    "DELETE FROM plan_artifacts WHERE workspace=? AND session_id=?",
                    (workspace, session_id),
                )
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE workspace=? AND session_id=?",
                    (workspace, session_id),
                )
        logger.info(f"[HistoryDB] 删除会话 '{session_id}' 的 {cur.rowcount} 条消息, workspace={workspace}")
        return cur.rowcount
    
    def delete_workspace(self, workspace: str) -> int:
        """删除工作空间的所有消息
        
        Args:
            workspace: 工作空间名称
        
        Returns:
            删除的消息数量
        """
        with self._lock:
            self._ensure_conn()
            with self._conn:
                try:
                    self._conn.execute(
                        "DELETE FROM messages_fts WHERE rowid IN (SELECT id FROM messages WHERE workspace=?)",
                        (workspace,),
                    )
                except Exception:
                    pass
                self._conn.execute(
                    "DELETE FROM plan_artifacts WHERE workspace=?",
                    (workspace,),
                )
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE workspace=?",
                    (workspace,),
                )
        logger.info(f"[HistoryDB] 删除工作空间 '{workspace}' 的 {cur.rowcount} 条消息")
        return cur.rowcount
    
    def delete_before(self, workspace: str, keep_count: int) -> int:
        """保留最近 N 条消息，删除其余
        
        Args:
            workspace: 工作空间名称
            keep_count: 保留数量
        
        Returns:
            删除的消息数量
        """
        if keep_count <= 0:
            return self.delete_workspace(workspace)
        
        with self._lock:
            self._ensure_conn()
            with self._conn:
                row = self._conn.execute(
                    "SELECT id FROM messages WHERE workspace=? ORDER BY id DESC LIMIT 1 OFFSET ?",
                    (workspace, keep_count - 1),
                ).fetchone()
                if not row:
                    return 0
                cutoff_id = row[0]
                ids = [r[0] for r in self._conn.execute(
                    "SELECT id FROM messages WHERE workspace=? AND id < ?",
                    (workspace, cutoff_id),
                ).fetchall()]
                if not ids:
                    return 0
                placeholders = ",".join("?" for _ in ids)
                try:
                    self._conn.execute(f"DELETE FROM messages_fts WHERE rowid IN ({placeholders})", ids)
                except Exception:
                    pass
                cur = self._conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    ids,
                )
        
        logger.info(f"[HistoryDB] delete_before: workspace={workspace}, 保留最近 {keep_count} 条，删除 {cur.rowcount} 条旧消息")
        return cur.rowcount
    
    def purge(self, workspace: str = "") -> int:
        """清空历史消息
        
        Args:
            workspace: 工作空间名称（空则清空所有）
        
        Returns:
            删除的消息数量
        """
        with self._lock:
            self._ensure_conn()
            with self._conn:
                if workspace:
                    try:
                        self._conn.execute(
                            "DELETE FROM messages_fts WHERE rowid IN (SELECT id FROM messages WHERE workspace=?)",
                            (workspace,),
                        )
                    except Exception:
                        pass
                    self._conn.execute(
                        "DELETE FROM plan_artifacts WHERE workspace=?",
                        (workspace,),
                    )
                    cur = self._conn.execute(
                        "DELETE FROM messages WHERE workspace=?",
                        (workspace,),
                    )
                    logger.info(f"[HistoryDB] 已清除 workspace={workspace}, 共 {cur.rowcount} 条消息")
                else:
                    self._conn.execute("DELETE FROM plan_artifacts")
                    cur = self._conn.execute("DELETE FROM messages")
                    try:
                        self._conn.execute("DELETE FROM messages_fts")
                    except Exception:
                        pass
                    logger.info(f"[HistoryDB] 已清除所有消息, 共 {cur.rowcount} 条")
        
        return cur.rowcount
    
    def delete_by_ids(self, ids: list[int]) -> int:
        """按 ID 列表删除消息
        
        Args:
            ids: 消息ID列表
        
        Returns:
            删除的消息数量
        """
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            self._ensure_conn()
            with self._conn:
                cur = self._conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    list(ids),
                )
                try:
                    self._conn.execute(f"DELETE FROM messages_fts WHERE rowid IN ({placeholders})", list(ids))
                except Exception:
                    pass
        logger.info(f"[HistoryDB] 删除 {cur.rowcount} 条消息")
        return cur.rowcount
    
    # === 统计操作 ===
    
    def count(self, workspace: str = "", session_id: str = "",
              keyword: str = "", date_from: str = "", date_to: str = "") -> int:
        """统计消息数量
        
        Args:
            workspace: 工作空间（空则统计所有）
            session_id: 会话ID（空则统计所有）
            keyword: 搜索关键词（空则不过滤）
            date_from: 起始日期（空则不过滤）
            date_to: 结束日期（空则不过滤）
        
        Returns:
            消息数量
        """
        with self._lock:
            self._ensure_conn()
            
            conditions = []
            params = []
            
            if workspace:
                conditions.append("workspace=?")
                params.append(workspace)
            if session_id:
                conditions.append("session_id=?")
                params.append(session_id)
            if date_from:
                conditions.append("ts >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("ts <= ?")
                params.append(date_to)
            if keyword:
                conditions.append("content LIKE ?")
                params.append(f"%{keyword}%")
            
            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM messages {where_clause}",
                params,
            ).fetchone()
        
        return row[0] if row else 0
    
    def list_sessions(self, workspace: str = "") -> list[dict]:
        """列出所有会话（含消息数、最后更新时间）
        
        Args:
            workspace: 工作空间（空则列出所有）
        
        Returns:
            会话列表：[{"workspace": ..., "session_id": ..., "message_count": ..., "updated_at": ...}]
        """
        with self._lock:
            self._ensure_conn()
            if workspace:
                rows = self._conn.execute(
                    """SELECT workspace, session_id, COUNT(*) as message_count, MAX(ts) as updated_at
                       FROM messages
                       WHERE workspace=?
                       GROUP BY workspace, session_id
                       ORDER BY updated_at DESC""",
                    (workspace,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT workspace, session_id, COUNT(*) as message_count, MAX(ts) as updated_at
                       FROM messages
                       GROUP BY workspace, session_id
                       ORDER BY updated_at DESC"""
                ).fetchall()
        
        return [
            {
                "workspace": ws,
                "session_id": sid,
                "message_count": count,
                "updated_at": updated_at
            }
            for ws, sid, count, updated_at in rows
        ]
    
    def get_latest_session(self, workspace: str) -> Optional[str]:
        """获取指定工作空间最新的会话ID
        
        Args:
            workspace: 工作空间名称
        
        Returns:
            最新的会话ID，如果没有会话则返回 None
        """
        with self._lock:
            self._ensure_conn()
            row = self._conn.execute(
                "SELECT session_id FROM messages WHERE workspace=? ORDER BY id DESC LIMIT 1",
                (workspace,),
            ).fetchone()
            return row[0] if row else None
    
    def get_session_name(self, workspace: str, session_id: str) -> Optional[str]:
        """获取会话名称（从第一条 system 消息的 metadata 中提取）
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
        
        Returns:
            会话名称（如果没有则返回 None）
        """
        with self._lock:
            self._ensure_conn()
            row = self._conn.execute(
                "SELECT metadata FROM messages WHERE workspace=? AND session_id=? AND role='system' ORDER BY id LIMIT 1",
                (workspace, session_id),
            ).fetchone()
        
        if row and row[0]:
            try:
                meta = json.loads(row[0])
                return meta.get("name")
            except json.JSONDecodeError:
                pass
        return None
    
    def update_session_name(self, workspace: str, session_id: str, name: str):
        """更新会话名称（存储在第一条 system 消息的 metadata 中）
        
        Args:
            workspace: 工作空间名称
            session_id: 会话ID
            name: 会话名称
        """
        with self._lock:
            self._ensure_conn()
            with self._conn:
                row = self._conn.execute(
                    "SELECT id, metadata FROM messages WHERE workspace=? AND session_id=? AND role='system' ORDER BY id LIMIT 1",
                    (workspace, session_id),
                ).fetchone()
                if row:
                    mid, raw_meta = row
                    try:
                        meta = json.loads(raw_meta) if raw_meta else {}
                    except json.JSONDecodeError:
                        meta = {}
                    meta["name"] = name
                    self._conn.execute(
                        "UPDATE messages SET metadata=? WHERE id=?",
                        (json.dumps(meta, ensure_ascii=False), mid),
                    )
                    logger.debug(f"[HistoryDB] 更新会话名称: {session_id} -> {name}")
    
    # === 辅助方法 ===
    
    def _parse_messages(self, rows: list) -> list[dict]:
        """解析消息行"""
        return [_message_from_history_row(role, content, metadata, ts) for role, content, metadata, ts in rows]
    
    def list_for_review(self, workspace: str, limit: int = 100) -> list[dict]:
        """列出消息供审核（含 id），用于选择性删除
        
        Args:
            workspace: 工作空间名称
            limit: 限制数量
        
        Returns:
            消息列表
        """
        with self._lock:
            self._ensure_conn()
            rows = self._conn.execute(
                "SELECT id, role, content, ts FROM messages WHERE workspace=? ORDER BY id LIMIT ?",
                (workspace, limit),
            ).fetchall()
        
        return [
            {"id": row[0], "role": row[1], "content": row[2][:200], "ts": row[3]}
            for row in rows
        ]
    
    # === 生命周期 ===
    
    def flush(self, timeout: float = 5.0):
        """等待所有异步写入任务完成
        
        Args:
            timeout: 超时时间
        """
        if self._async_write and self._async_writer:
            self._async_writer.flush(timeout)
    
    def get_async_stats(self) -> dict:
        """获取异步写入统计信息
        
        Returns:
            统计字典（如果未启用异步写入，返回空字典）
        """
        if self._async_write and self._async_writer:
            return self._async_writer.get_stats()
        return {}
    
    def close(self):
        """关闭数据库连接"""
        if self._closed:
            return
        
        # 停止异步写入器
        if self._async_writer:
            self._async_writer.stop()
        
        with self._lock:
            try:
                self._conn.close()
                self._closed = True
                logger.debug(f"[HistoryDB] 已关闭: {self.db_path}")
            except Exception as e:
                logger.warning(f"[HistoryDB] 关闭失败: {e}")
    
    def is_closed(self) -> bool:
        """检查连接是否已关闭"""
        return self._closed
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# === 连接池管理器 ===

class HistoryDBPool:
    """历史数据库连接池（单例模式，按用户管理连接）
    
    使用方式：
        db = HistoryDBPool.get(username)
        db.append(workspace, session_id, role, content)
        HistoryDBPool.close(username)  # 关闭指定用户的连接
        HistoryDBPool.close_all()      # 关闭所有连接
    
    支持异步写入模式：
        db = HistoryDBPool.get(username, async_write=True)
    """
    
    _instance = None
    _lock = threading.Lock()
    _pools: dict[str, HistoryDB] = {}  # username -> HistoryDB
    _access_time: dict[str, float] = {}  # username -> last access time
    _async_write_default = False  # 默认关闭异步写入
    _max_connections = 100  # 最大连接数上限
    
    @classmethod
    def set_async_write_default(cls, enabled: bool):
        """设置默认是否启用异步写入
        
        Args:
            enabled: 是否启用
        """
        cls._async_write_default = enabled
        logger.info(f"[HistoryDBPool] 异步写入默认设置: {enabled}")
    
    @classmethod
    def _evict_if_needed(cls):
        """如果连接数超过上限，淘汰最久未使用的连接"""
        if len(cls._pools) >= cls._max_connections:
            # 淘汰最久未使用的 10 个连接
            sorted_users = sorted(cls._access_time.items(), key=lambda x: x[1])
            evict_count = min(10, len(sorted_users))
            for username, _ in sorted_users[:evict_count]:
                try:
                    cls._pools[username].close()
                    del cls._pools[username]
                    del cls._access_time[username]
                    logger.info(f"[HistoryDBPool] 淘汰连接: username={username}")
                except Exception as e:
                    logger.warning(f"[HistoryDBPool] 淘汰连接失败: username={username}, error={e}")
    
    @classmethod
    def get(cls, username: str, async_write: bool | None = None) -> HistoryDB:
        """获取用户的数据库连接（复用）
        
        Args:
            username: 用户名
            async_write: 是否启用异步写入
                         None: 从配置读取（Web 端默认 true，CLI 端默认 false）
                         True: 强制启用异步写入
                         False: 强制使用同步写入
        
        Returns:
            HistoryDB 实例
        """
        if async_write is None:
            # 从全局默认设置读取（Web 端会在启动时设置为 True）
            async_write = cls._async_write_default
        
        with cls._lock:
            # 更新访问时间
            import time
            cls._access_time[username] = time.time()
            
            if username not in cls._pools:
                # 检查是否需要淘汰
                cls._evict_if_needed()
                
                from ..config import user_data_dir
                db_path = user_data_dir(username) / "history.db"
                cls._pools[username] = HistoryDB(db_path, async_write=async_write)
                logger.debug(f"[HistoryDBPool] 创建连接: username={username}, async_write={async_write}")
            return cls._pools[username]
    
    @classmethod
    def close(cls, username: str):
        """关闭指定用户的数据库连接
        
        Args:
            username: 用户名
        """
        with cls._lock:
            if username in cls._pools:
                cls._pools[username].close()
                del cls._pools[username]
                cls._access_time.pop(username, None)
                logger.debug(f"[HistoryDBPool] 关闭连接: username={username}")
    
    @classmethod
    def close_all(cls):
        """关闭所有数据库连接"""
        with cls._lock:
            for username, db in cls._pools.items():
                try:
                    db.close()
                except Exception as e:
                    logger.warning(f"[HistoryDBPool] 关闭连接失败: username={username}, error={e}")
            cls._pools.clear()
            cls._access_time.clear()
            logger.info("[HistoryDBPool] 已关闭所有连接")
    
    @classmethod
    def stats(cls) -> dict:
        """获取连接池统计信息"""
        with cls._lock:
            return {
                "total_connections": len(cls._pools),
                "max_connections": cls._max_connections,
                "users": list(cls._pools.keys())
            }
