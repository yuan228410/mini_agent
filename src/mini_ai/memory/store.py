"""三层记忆存储：情景层 / 长期层 / 用户画像，支持 global→user→workspace 三层级合并"""
import json
import threading
from datetime import datetime
from ..utils import _UTC8
from pathlib import Path

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False
    _FALLBACK_LOCK = threading.Lock()

def _file_lock_path(filepath: Path) -> Path:
    """同一目录下用 .lock 文件做跨进程/跨实例文件锁。"""
    return filepath.with_suffix(filepath.suffix + ".lock")

def _read_locked(filepath: Path) -> str:
    """加共享锁读取文件，防止读到写入一半的内容。"""
    if not filepath.exists():
        return ""
    if not _HAS_FCNTL:
        try:
            return filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
    lock_path = _file_lock_path(filepath)
    try:
        with lock_path.open("w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
            try:
                return filepath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                from .logger import logger
                logger.warning(f"[MemoryStore] 读取 {filepath} 失败: {e}")
                return ""
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    except OSError:
        # lock 文件无法创建时回退到无锁读取
        try:
            return filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

def _write_locked(filepath: Path, content: str) -> None:
    """加排他锁写入文件，防止跨实例并发写入冲突。"""
    lock_path = _file_lock_path(filepath)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not _HAS_FCNTL:
        with _FALLBACK_LOCK:
            filepath.write_text(content, encoding="utf-8")
        return
    with lock_path.open("w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            filepath.write_text(content, encoding="utf-8")
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

class _exclusive_lock:
    """文件排他锁上下文管理器，用于 read-modify-write 和 backup+write 的原子操作。

    获取文件级 LOCK_EX，在 with 块内可安全地读写同一文件，跨实例/跨线程均安全。
    """
    __slots__ = ("_filepath", "_lf", "_acquired")

    def __init__(self, filepath: Path):
        self._filepath = filepath
        self._lf = None
        self._acquired = False

    def __enter__(self):
        lock_path = _file_lock_path(self._filepath)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if not _HAS_FCNTL:
            _FALLBACK_LOCK.acquire()
            self._acquired = True
            return
        self._lf = lock_path.open("w")
        try:
            fcntl.flock(self._lf.fileno(), fcntl.LOCK_EX)
            self._acquired = True
        except Exception:
            # flock 失败时确保关闭文件句柄
            if self._lf is not None:
                try:
                    self._lf.close()
                except Exception:
                    pass
                self._lf = None
            raise

    def __exit__(self, *exc):
        try:
            if self._lf is not None:
                try:
                    fcntl.flock(self._lf.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                finally:
                    try:
                        self._lf.close()
                    except Exception:
                        pass
                    self._lf = None
            elif self._acquired and not _HAS_FCNTL:
                _FALLBACK_LOCK.release()
        finally:
            self._acquired = False

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
        self._ensure()

    def _ensure(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._episode_dir.mkdir(parents=True, exist_ok=True)
        for f, default in [
            (self.memory_file, "# 长期记忆\n\n"),
            (self.user_file, "# 用户画像\n\n"),
        ]:
            if not f.exists():
                _write_locked(f, default)
        if self._global_dir:
            self._global_dir.mkdir(parents=True, exist_ok=True)
            for f, default in [
                (self._global_dir / "MEMORY.md", "# 长期记忆\n\n"),
                (self._global_dir / "USER.md", "# 用户画像\n\n"),
            ]:
                if not f.exists():
                    _write_locked(f, default)
        if self._workspace_dir:
            self._workspace_dir.mkdir(parents=True, exist_ok=True)
            for f, default in [
                (self._workspace_dir / "MEMORY.md", "# 长期记忆\n\n"),
                (self._workspace_dir / "USER.md", "# 用户画像\n\n"),
            ]:
                if not f.exists():
                    _write_locked(f, default)

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
        return _read_locked(self._today_path())

    def append_today(self, content: str) -> None:
        p = self._today_path()
        with _exclusive_lock(p):
            existing = p.read_text(encoding="utf-8") if p.exists() else f"# {p.stem}\n"
            p.write_text(existing.rstrip() + "\n\n" + content.strip() + "\n", encoding="utf-8")
        from .logger import logger
        logger.debug(f"[MemoryStore] append_today: {len(content)} 字 -> {p.name}")

    # ── 长期层 ──
    def _read_file(self, path: Path) -> str:
        return _read_locked(path)

    def read_memory(self) -> str:
        texts = [self._read_file(p / "MEMORY.md") for p in self._tier_paths()]
        merged = _merge_sections(texts)
        return merged

    def has_memory(self) -> bool:
        content = self.read_memory().strip()
        return bool(content and content != "# 长期记忆")

    def write_memory(self, content: str) -> None:
        with _exclusive_lock(self.memory_file):
            if self.memory_file.exists():
                try:
                    import shutil
                    shutil.copy2(self.memory_file, self.memory_file.with_suffix(".md.bak"))
                except OSError:
                    pass
            self.memory_file.write_text(content.strip() + "\n", encoding="utf-8")
        from .logger import logger
        logger.info(f"[MemoryStore] write_memory: {len(content)} 字 -> {self.memory_file.name}")

    def write_memory_at(self, content: str, level: str) -> None:
        d = self.get_tier_dir(level)
        if not d:
            logger.warning(f"[MemoryStore] write_memory_at: level={level} 无对应目录")
            return
        d.mkdir(parents=True, exist_ok=True)
        _write_locked(d / "MEMORY.md", content.strip() + "\n")
        from .logger import logger
        logger.info(f"[MemoryStore] write_memory_at: {len(content)} 字, level={level}")

    # ── 用户画像 ──
    def read_user(self) -> str:
        texts = [self._read_file(p / "USER.md") for p in self._tier_paths()]
        merged = _merge_sections(texts)
        return merged

    def has_user(self) -> bool:
        content = self.read_user().strip()
        return bool(content and content != "# 用户画像")

    def write_user(self, content: str) -> None:
        with _exclusive_lock(self.user_file):
            if self.user_file.exists():
                try:
                    import shutil
                    shutil.copy2(self.user_file, self.user_file.with_suffix(".md.bak"))
                except OSError:
                    pass
            self.user_file.write_text(content.strip() + "\n", encoding="utf-8")
        from .logger import logger
        logger.info(f"[MemoryStore] write_user: {len(content)} 字 -> {self.user_file.name}")

    def write_user_at(self, content: str, level: str) -> None:
        d = self.get_tier_dir(level)
        if not d:
            logger.warning(f"[MemoryStore] write_user_at: level={level} 无对应目录")
            return
        d.mkdir(parents=True, exist_ok=True)
        _write_locked(d / "USER.md", content.strip() + "\n")
        from .logger import logger
        logger.info(f"[MemoryStore] write_user_at: {len(content)} 字, level={level}")
