"""历史消息 SQLite 存储 — FTS5 全文搜索支持"""
import atexit
import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ..logger import logger

_UTC8 = timezone(timedelta(hours=8))


class HistoryDB:

    def __init__(self, db_path: Path, workspace: str = "default"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self._lock = threading.Lock()
        self._fts_available = True
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        atexit.register(self.close)
        logger.debug(f"[HistoryDB] 初始化: workspace={workspace}, path={db_path}")

    def _init_schema(self):
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace TEXT NOT NULL,
                    session_id TEXT DEFAULT '',
                    ts TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    metadata TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_messages_workspace ON messages(workspace);
                CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
            """)
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content=messages, content_rowid=id)"
                )
            except sqlite3.OperationalError:
                self._fts_available = False
                logger.warning("[HistoryDB] FTS5 不可用，全文搜索将退化为模糊匹配")
                pass

    def _ensure_conn(self):
        try:
            self._conn.execute("SELECT 1")
        except Exception:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema()

    def append(self, role: str, content: str, session_id: str = "", metadata: str = ""):
        ts = datetime.now(_UTC8).isoformat()
        content_preview = (content or "")[:80].replace("\n", " ")
        logger.debug(f"[HistoryDB] append: role={role}, sid={session_id}, len={len(content or '')}, preview={content_preview}")
        with self._lock:
            self._ensure_conn()
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO messages (workspace, session_id, ts, role, content, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                    (self.workspace, session_id, ts, role, content, metadata),
                )
                try:
                    self._conn.execute(
                        "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                        (cur.lastrowid, content),
                    )
                except sqlite3.OperationalError:
                    if self._fts_available:
                        self._fts_available = False
                        logger.warning("[HistoryDB] FTS5 写入失败，后续搜索将退化为模糊匹配")
                    pass



    def purge(self):
        """彻底删除所有历史消息（不可恢复）"""
        with self._lock:
            self._ensure_conn()
            with self._conn:
                self._conn.execute(
                    "DELETE FROM messages WHERE workspace=?",
                    (self.workspace,),
                )
        logger.info(f"[HistoryDB] 已清除 workspace={self.workspace}")


    def delete_by_ids(self, ids: list[int]):
        """按 ID 列表删除消息"""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            self._ensure_conn()
            with self._conn:
                cur = self._conn.execute(
                    f"DELETE FROM messages WHERE workspace=? AND id IN ({placeholders})",
                    [self.workspace] + list(ids),
                )
                try:
                    self._conn.execute(f"DELETE FROM messages_fts WHERE rowid IN ({placeholders})", list(ids))
                except Exception:
                    pass
        logger.info(f"[HistoryDB] 删除 {cur.rowcount} 条消息")
        return cur.rowcount

    def update_session_name(self, session_id: str, name: str):
        """更新指定会话的 system 消息 metadata 中的 name"""
        with self._lock:
            self._ensure_conn()
            with self._conn:
                row = self._conn.execute(
                    "SELECT id, metadata FROM messages WHERE workspace=? AND session_id=? AND role='system' ORDER BY id LIMIT 1",
                    (self.workspace, session_id),
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

    def delete_by_session(self, session_id: str) -> int:
        """删除指定会话的所有消息"""
        with self._lock:
            self._ensure_conn()
            with self._conn:
                try:
                    self._conn.execute(
                        "DELETE FROM messages_fts WHERE rowid IN (SELECT id FROM messages WHERE workspace=? AND session_id=?)",
                        (self.workspace, session_id),
                    )
                except Exception:
                    pass
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE workspace=? AND session_id=?",
                    (self.workspace, session_id),
                )
        logger.info(f"[HistoryDB] 删除会话 '{session_id}' 的 {cur.rowcount} 条消息")
        return cur.rowcount

    def delete_before(self, keep_count: int):
        """保留最近 N 条消息，删除其余"""
        if keep_count <= 0:
            with self._lock:
                self._ensure_conn()
                with self._conn:
                    cur = self._conn.execute(
                        "DELETE FROM messages WHERE workspace=?",
                        (self.workspace,),
                    )
                    try:
                        self._conn.execute(
                            "DELETE FROM messages_fts WHERE rowid IN (SELECT id FROM messages WHERE workspace=?)",
                            (self.workspace,),
                        )
                    except Exception:
                        pass
            logger.info(f"[HistoryDB] 删除所有消息: {cur.rowcount} 条")
            return cur.rowcount
        with self._lock:
            self._ensure_conn()
            with self._conn:
                row = self._conn.execute(
                    "SELECT id FROM messages WHERE workspace=? ORDER BY id DESC LIMIT 1 OFFSET ?",
                    (self.workspace, keep_count - 1),
                ).fetchone()
                if not row:
                    return 0
                cutoff_id = row[0]
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE workspace=? AND id < ?",
                    (self.workspace, cutoff_id),
                )
                try:
                    self._conn.execute("DELETE FROM messages_fts WHERE rowid < ?", (cutoff_id,))
                except Exception:
                    pass
        logger.info(f"[HistoryDB] delete_before: 保留最近 {keep_count} 条，删除 {cur.rowcount} 条旧消息, workspace={self.workspace}")
        return cur.rowcount

    def list_for_review(self, limit: int = 100) -> list[dict]:
        """列出消息供审核（含 id），用于选择性删除"""
        with self._lock:
            self._ensure_conn()
            rows = self._conn.execute(
                "SELECT id, role, content, ts FROM messages WHERE workspace=? ORDER BY id",
                (self.workspace,),
            ).fetchall()
        results = []
        for row in rows:
            msg = {"id": row[0], "role": row[1], "content": row[2][:200], "ts": row[3]}
            results.append(msg)
        return results

    def search(self, keyword: str, date_from: str = "", date_to: str = "",
               workspace: str = "", limit: int = 20) -> list[dict]:
        ws = workspace or self.workspace
        logger.debug(f"[HistoryDB] search: keyword={keyword[:30]}, workspace={ws}, limit={limit}")
        conditions = ["workspace=?"]
        params = [ws]

        if date_from:
            conditions.append("ts >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("ts <= ?")
            params.append(date_to)

        if keyword:
            conditions.append("content LIKE ?")
            params.append(f"%{keyword}%")

        with self._lock:
            self._ensure_conn()
            rows = self._conn.execute(
                f"SELECT id, ts, role, content FROM messages "
                f"WHERE {' AND '.join(conditions)} "
                f"ORDER BY ts DESC LIMIT ?",
                params + [limit],
            ).fetchall()

        return [{"id": id, "ts": ts, "role": role, "content": content} for id, ts, role, content in rows]

    def load_all(self, session_id: str = "", limit: int = 0) -> list[dict]:
        logger.debug(f"[HistoryDB] load_all: sid={session_id}, limit={limit}, workspace={self.workspace}")
        with self._lock:
            self._ensure_conn()
            if session_id:
                if limit > 0:
                    rows = self._conn.execute(
                        "SELECT role, content, metadata, ts FROM (SELECT id, role, content, metadata, ts FROM messages WHERE workspace=? AND session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                        (self.workspace, session_id, limit),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT role, content, metadata, ts FROM messages WHERE workspace=? AND session_id=? ORDER BY id",
                        (self.workspace, session_id),
                    ).fetchall()
            else:
                if limit > 0:
                    rows = self._conn.execute(
                        "SELECT role, content, metadata, ts FROM (SELECT id, role, content, metadata, ts FROM messages WHERE workspace=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                        (self.workspace, limit),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT role, content, metadata, ts FROM messages WHERE workspace=? ORDER BY id",
                        (self.workspace,),
                    ).fetchall()
        results = []
        for role, content, metadata, ts in rows:
            msg = {"role": role, "content": content, "timestamp": ts[:19] if ts else ""}
            if metadata:
                try:
                    extra = json.loads(metadata)
                    # 只合并非核心字段，防止 metadata 中的 role/content 覆盖真实值
                    for k, v in extra.items():
                        if k not in ("role", "content"):
                            msg[k] = v
                except json.JSONDecodeError:
                    pass
            results.append(msg)
        logger.debug(f"[HistoryDB] load_all 返回: {len(results)} 条消息")
        return results

    def count(self) -> int:
        with self._lock:
            self._ensure_conn()
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE workspace=?",
                (self.workspace,),
            ).fetchone()
        return row[0] if row else 0

    def close(self):
        try:
            self._conn.close()
            logger.debug(f"[HistoryDB] 已关闭: {self.db_path}")
        except Exception:
            pass
