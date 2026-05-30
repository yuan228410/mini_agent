"""三层记忆存储：情景层 / 长期层 / 用户画像，支持 global→user→workspace 三层级合并"""
import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

_UTC8 = timezone(timedelta(hours=8))


def _merge_sections(texts: list[str]) -> str:
    """按 ## 标题拆分，同名 section last-wins，整体叠加。"""
    all_sections: dict[str, str] = {}
    header = ""
    for text in texts:
        if not text.strip():
            continue
        current_title = ""
        current_body = ""
        for line in text.split("\n"):
            if line.startswith("## "):
                if current_title:
                    all_sections[current_title] = current_body.rstrip()
                elif current_body.strip():
                    header = current_body.rstrip()
                current_title = line.strip()
                current_body = line + "\n"
            else:
                current_body += line + "\n"
        if current_title:
            all_sections[current_title] = current_body.rstrip()
        elif current_body.strip():
            header = current_body.rstrip()
    parts = [header] if header else []
    for _, body in all_sections.items():
        parts.append(body)
    return "\n\n".join(parts)


class MemoryStore:
    """记忆存储（情景层 + 长期层 + 用户画像）。

    支持三层级合并读取：global → user → workspace
    - 情景层: YYYY-MM-DD.md — 每日情景记忆短文（per-session）
    - 长期层: MEMORY.md — 常驻上下文的长期记忆（三层级合并）
    - 用户画像: USER.md（三层级合并）
    """

    def __init__(self, memory_dir: Path, episode_dir: Path | None = None,
                 global_memory_dir: Path | None = None,
                 workspace_memory_dir: Path | None = None):
        self.memory_dir = Path(memory_dir)
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.user_file = self.memory_dir / "USER.md"
        self._episode_dir = Path(episode_dir) if episode_dir else self.memory_dir
        self._global_dir = Path(global_memory_dir) if global_memory_dir else None
        self._workspace_dir = Path(workspace_memory_dir) if workspace_memory_dir else None
        self._lock = threading.Lock()
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
        if self._global_dir:
            self._global_dir.mkdir(parents=True, exist_ok=True)
            for f, default in [
                (self._global_dir / "MEMORY.md", "# 长期记忆\n\n"),
                (self._global_dir / "USER.md", "# 用户画像\n\n"),
            ]:
                if not f.exists():
                    f.write_text(default, encoding="utf-8")
        if self._workspace_dir:
            self._workspace_dir.mkdir(parents=True, exist_ok=True)
            for f, default in [
                (self._workspace_dir / "MEMORY.md", "# 长期记忆\n\n"),
                (self._workspace_dir / "USER.md", "# 用户画像\n\n"),
            ]:
                if not f.exists():
                    f.write_text(default, encoding="utf-8")

    def _tier_paths(self) -> list[Path]:
        paths = []
        if self._global_dir:
            paths.append(self._global_dir)
        paths.append(self.memory_dir)
        if self._workspace_dir and self._workspace_dir != self.memory_dir:
            paths.append(self._workspace_dir)
        return paths

    def get_tier_dir(self, level: str) -> Path | None:
        if level == "global":
            return self._global_dir
        if level == "user":
            return self.memory_dir
        if level == "workspace":
            return self._workspace_dir
        return None

    # ── 情景层 ──
    def _today_path(self) -> Path:
        return self._episode_dir / f"{datetime.now(_UTC8).strftime('%Y-%m-%d')}.md"

    def read_today(self) -> str:
        p = self._today_path()
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def append_today(self, content: str) -> None:
        with self._lock:
            p = self._today_path()
            existing = p.read_text(encoding="utf-8") if p.exists() else f"# {p.stem}\n"
            p.write_text(existing.rstrip() + "\n\n" + content.strip() + "\n", encoding="utf-8")

    # ── 长期层 ──
    def _read_file(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            from .logger import logger
            logger.warning(f"[MemoryStore] 读取 {path} 失败: {e}")
            return ""

    def read_memory(self) -> str:
        texts = [self._read_file(p / "MEMORY.md") for p in self._tier_paths()]
        merged = _merge_sections(texts)
        return merged

    def has_memory(self) -> bool:
        content = self.read_memory().strip()
        return bool(content and content != "# 长期记忆")

    def write_memory(self, content: str) -> None:
        with self._lock:
            if self.memory_file.exists():
                try:
                    import shutil
                    shutil.copy2(self.memory_file, self.memory_file.with_suffix(".md.bak"))
                except OSError:
                    pass
            self.memory_file.write_text(content.strip() + "\n", encoding="utf-8")

    def write_memory_at(self, content: str, level: str) -> None:
        d = self.get_tier_dir(level)
        if not d:
            return
        d.mkdir(parents=True, exist_ok=True)
        with self._lock:
            (d / "MEMORY.md").write_text(content.strip() + "\n", encoding="utf-8")

    # ── 用户画像 ──
    def read_user(self) -> str:
        texts = [self._read_file(p / "USER.md") for p in self._tier_paths()]
        merged = _merge_sections(texts)
        return merged

    def has_user(self) -> bool:
        content = self.read_user().strip()
        return bool(content and content != "# 用户画像")

    def write_user(self, content: str) -> None:
        with self._lock:
            if self.user_file.exists():
                try:
                    import shutil
                    shutil.copy2(self.user_file, self.user_file.with_suffix(".md.bak"))
                except OSError:
                    pass
            self.user_file.write_text(content.strip() + "\n", encoding="utf-8")

    def write_user_at(self, content: str, level: str) -> None:
        d = self.get_tier_dir(level)
        if not d:
            return
        d.mkdir(parents=True, exist_ok=True)
        with self._lock:
            (d / "USER.md").write_text(content.strip() + "\n", encoding="utf-8")
