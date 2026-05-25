"""会话管理：命名保存、恢复、列表"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .logger import logger

_UTC8 = timezone(timedelta(hours=8))


class SessionManager:
    def __init__(self, sessions_dir: Path):
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.current = None

    def save(self, name: str, messages: list[dict]) -> str:
        """保存当前对话为命名会话"""
        name = name.strip().replace(" ", "_")
        if not name:
            return "Error: 会话名不能为空"

        path = self.dir / f"{name}.jsonl"
        ts = datetime.now(_UTC8).isoformat(timespec="seconds")

        rows = []
        for m in messages:
            if m["role"] == "system":
                continue
            rows.append({"ts": ts, "role": m["role"], "content": m.get("content")})
            ts = None

        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        self.current = name
        logger.info(f"[会话] 已保存 '{name}' ({len(rows)} 条消息)")
        return f"会话 '{name}' 已保存（{len(rows)} 条消息）"

    def load(self, name: str) -> list[dict] | None:
        """加载命名会话，返回消息列表"""
        name = name.strip().replace(" ", "_")
        path = self.dir / f"{name}.jsonl"
        if not path.exists():
            logger.warning(f"[会话] '{name}' 不存在")
            return None

        messages = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                messages.append({"role": row["role"], "content": row.get("content")})
            except (json.JSONDecodeError, KeyError):
                continue

        self.current = name
        logger.info(f"[会话] 已加载 '{name}' ({len(messages)} 条消息)")
        return messages

    def list_sessions(self) -> list[dict]:
        """列出所有已保存的会话"""
        sessions = []
        for p in sorted(self.dir.glob("*.jsonl")):
            stat = p.stat()
            lines = len(p.read_text(encoding="utf-8").splitlines())
            sessions.append({
                "name": p.stem,
                "messages": lines,
                "size_kb": round(stat.st_size / 1024, 1),
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=_UTC8).isoformat(timespec="minutes"),
            })
        return sessions

    def render_list(self) -> str:
        sessions = self.list_sessions()
        if not sessions:
            return "暂无已保存的会话。"
        current = self.current
        lines = [f"{'*' if s['name'] == current else ' '} {s['name']:20s} {s['messages']:>4} 条消息  {s['size_kb']:>6} KB  {s['mtime']}" for s in sessions]
        return "\n".join(lines)
