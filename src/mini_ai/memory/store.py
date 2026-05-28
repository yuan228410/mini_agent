"""三层记忆存储：原始层 / 情景记忆 / 长期记忆"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

_UTC8 = timezone(timedelta(hours=8))

class MemoryStore:
    """记忆存储（情景层 + 长期层 + 用户画像）。

    情景层: YYYY-MM-DD.md — 每日情景记忆短文
    长期层: MEMORY.md — 常驻上下文的长期记忆
    用户画像: USER.md
    """

    def __init__(self, memory_dir: Path, episode_dir: Path | None = None):
        self.memory_dir = Path(memory_dir)
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.user_file = self.memory_dir / "USER.md"
        self._episode_dir = Path(episode_dir) if episode_dir else self.memory_dir
        self._ensure()

    def _ensure(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._episode_dir.mkdir(parents=True, exist_ok=True)
        for f, default in [
            (self.memory_file, "# 长期记忆\n\n"),
            (self.user_file, "# 用户画像\n\n"),
        ]:
            if not f.exists():
                f.write_text(default, encoding="utf-8")

    # ── 情景层 ──
    def _today_path(self) -> Path:
        return self._episode_dir / f"{datetime.now(_UTC8).strftime('%Y-%m-%d')}.md"

    def read_today(self) -> str:
        p = self._today_path()
        return p.read_text(encoding="utf-8") if p.exists() else ""

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