"""历史消息 SQLite 存储 — FTS5 全文搜索支持"""
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
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

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
                    metadata TEXT DEFAULT '',
                    archived INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_messages_workspace ON messages(workspace);
                CREATE INDEX IF NOT EXISTS idx_messages_archived ON messages(workspace, archived);
                CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
            """)
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content=messages, content_rowid=id)"
                )
            except sqlite3.OperationalError:
                pass

    def append(self, role: str, content: str, session_id: str = "", metadata: str = ""):
        ts = datetime.now(_UTC8).isoformat()
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO messages (workspace, session_id, ts, role, content, metadata, archived) VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (self.workspace, session_id, ts, role, content, metadata),
                )
                try:
                    self._conn.execute(
                        "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                        (cur.lastrowid, content),
                    )
                except sqlite3.OperationalError:
                    pass

    def load_unarchived(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, metadata FROM messages WHERE workspace=? AND archived=0 ORDER BY id",
                (self.workspace,),
            ).fetchall()
        results = []
        for role, content, metadata in rows:
            msg = {"role": role, "content": content}
            if metadata:
                try:
                    msg.update(json.loads(metadata))
                except json.JSONDecodeError:
                    pass
            results.append(msg)
        return results

    def mark_archived(self):
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE messages SET archived=1 WHERE workspace=? AND archived=0",
                    (self.workspace,),
                )
        logger.info(f"[HistoryDB] 已归档 workspace={self.workspace}")

    def search(self, keyword: str, date_from: str = "", date_to: str = "",
               workspace: str = "", limit: int = 20) -> list[dict]:
        ws = workspace or self.workspace
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
            rows = self._conn.execute(
                f"SELECT ts, role, content FROM messages "
                f"WHERE {' AND '.join(conditions)} "
                f"ORDER BY ts DESC LIMIT ?",
                params + [limit],
            ).fetchall()

        return [{"ts": ts, "role": role, "content": content} for ts, role, content in rows]

    def count(self, archived: bool = False) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE workspace=? AND archived=?",
                (self.workspace, int(archived)),
            ).fetchone()
        return row[0] if row else 0

    def close(self):
        self._conn.close()
