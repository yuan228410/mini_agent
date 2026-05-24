"""三层记忆存储：原始层 / 情景记忆 / 长期记忆"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

_UTC8 = timezone(timedelta(hours=8))

COMPACT_PROMPT = """将以下对话历史归档，输出三个部分：

<episode>
本次对话的关键记录（事实、结论、待办），用于每日回顾。保持简洁，只记录有价值的信息。
</episode>

<updated_memory>
如果本次对话产生了值得长期记住的信息（用户目标、重要决策、关键偏好、项目背景），更新长期记忆。格式：先写保留的旧记忆要点，再写新增/更新的内容。若无需更新则写"(无需更新)"。
</updated_memory>

<updated_user>
如果本次对话让你更了解用户的偏好、习惯、知识背景，更新用户画像。若无需更新则写"(无需更新)"。
</updated_user>

当前长期记忆：
{current_memory}

当前用户画像：
{current_user}

今天的已有记录：
{today_episode}

对话历史：
{old_conversation}"""


class MemoryStore:
    """三层记忆存储。

    原始层: history.jsonl — 完整对话日志，按 compact_event 标记归档状态
    情景层: YYYY-MM-DD.md — 每日情景记忆短文
    长期层: MEMORY.md — 常驻上下文的长期记忆
    """

    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.history_file = self.memory_dir / "history.jsonl"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.user_file = self.memory_dir / "USER.md"
        self._ensure()

    def _ensure(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        for f, default in [
            (self.history_file, ""),
            (self.memory_file, "# 长期记忆\n\n"),
            (self.user_file, "# 用户画像\n\n"),
        ]:
            if not f.exists():
                f.write_text(default, encoding="utf-8")

    # ── 原始层 ──
    def append(self, role: str, content: str | None) -> None:
        """写入一条对话到原始日志"""
        row = {
            "ts": datetime.now(_UTC8).isoformat(timespec="seconds"),
            "role": role,
            "content": content,
        }
        with self.history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def load_unarchived(self) -> list[dict]:
        """读取最后一个 compact_event 之后未归档的消息"""
        if not self.history_file.exists():
            return []
        rows = []
        with self.history_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        last = -1
        for i, r in enumerate(rows):
            if r.get("type") == "compact_event":
                last = i
        return [
            {"role": r["role"], "content": r["content"]}
            for r in rows[last + 1 :]
            if "role" in r and "content" in r
        ]

    # ── 情景层 ──
    def _today_path(self) -> Path:
        return self.memory_dir / f"{datetime.now(_UTC8).strftime('%Y-%m-%d')}.md"

    def read_today(self) -> str:
        p = self._today_path()
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def write_today(self, content: str) -> None:
        p = self._today_path()
        p.write_text(content.strip() + "\n", encoding="utf-8")

    def append_today(self, content: str) -> None:
        p = self._today_path()
        existing = p.read_text(encoding="utf-8") if p.exists() else f"# {p.stem}\n"
        p.write_text(existing.rstrip() + "\n\n" + content.strip() + "\n", encoding="utf-8")

    # ── 长期层 ──
    def read_memory(self) -> str:
        return self.memory_file.read_text(encoding="utf-8") if self.memory_file.exists() else ""

    def has_memory(self) -> bool:
        content = self.read_memory().strip()
        return bool(content and content != "# 长期记忆")

    def write_memory(self, content: str) -> None:
        self.memory_file.write_text(content.strip() + "\n", encoding="utf-8")

    # ── 用户画像 ──
    def read_user(self) -> str:
        return self.user_file.read_text(encoding="utf-8") if self.user_file.exists() else ""

    def has_user(self) -> bool:
        content = self.read_user().strip()
        return bool(content and content != "# 用户画像")

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content.strip() + "\n", encoding="utf-8")

    # ── 归档标记 ──
    def mark_compacted(self) -> None:
        row = {"ts": datetime.now(_UTC8).isoformat(timespec="seconds"), "type": "compact_event"}
        with self.history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")