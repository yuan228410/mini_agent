"""上下文组装器：从多个来源构建系统提示词"""
import threading
from pathlib import Path

from .logger import logger


class ContextBuilder:
    """按优先级组装系统提示词。

    组装顺序：
      1. 系统核心能力（硬编码）
      2. 身份定义（SOUL.md，三层级合并）
      3. 行为规范（RULES.md，三层级合并）
      4. 长期记忆 (MemoryStore)
      5. 技能列表 (SkillLoader)
      6. 项目规范 (CLAUDE.md / AGENTS.md)
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self._file_cache: dict[str, tuple[float, str]] = {}
        self._cache_lock = threading.Lock()

    def _read_cached(self, path: Path) -> str | None:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            with self._cache_lock:
                self._file_cache.pop(str(path), None)
            return None
        with self._cache_lock:
            cached = self._file_cache.get(str(path))
            if cached and cached[0] == mtime:
                return cached[1]
        if not path.exists():
            with self._cache_lock:
                self._file_cache.pop(str(path), None)
            return None
        # 使用 with 语句确保文件句柄正确关闭
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read().strip() or None
        except OSError:
            with self._cache_lock:
                self._file_cache.pop(str(path), None)
            return None
        with self._cache_lock:
            if text is not None:
                self._file_cache[str(path)] = (mtime, text)
            else:
                self._file_cache.pop(str(path), None)
        return text

    def build(self, memory_store=None, skill_loader=None, project_path: str = "", exclude_character: bool = False) -> str:
        parts = []

        # 1. 身份定义（SOUL.md）
        if not exclude_character and memory_store:
            soul = memory_store.read_soul()
            if soul:
                parts.append(soul)
                logger.debug(f"[Context] 注入身份定义: {len(soul)} 字")

        # 2. 系统核心能力（硬编码）
        if not exclude_character:
            from .core_prompt import get_core_prompt
            parts.append(get_core_prompt())

        # 3. 行为规范（RULES.md）
        if not exclude_character and memory_store:
            rules = memory_store.read_rules()
            if rules:
                parts.append(rules)
                logger.debug(f"[Context] 注入行为规范: {len(rules)} 字")

        # 4. 技能列表
        if skill_loader:
            skills_text = skill_loader.get_descriptions()
            if skills_text and skills_text != "(no skills available)":
                parts.append(f"## 可用技能\n\n{skills_text}")

        # 5. 长期记忆
        if memory_store:
            mem = memory_store.read_memory()
            if mem and mem.strip() != "# 长期记忆":
                parts.append(f"## 长期记忆\n\n{mem}")
                logger.debug(f"[Context] 注入长期记忆: {len(mem)} 字")

        # 6. 项目规范
        if project_path:
            parts.append("## 当前工作空间\n\n项目路径: " + project_path + "\n\n重要：执行命令时必须传 cwd=\"" + project_path + "\" 参数；读写文件使用绝对路径基于此目录。不要使用其他目录。")

        cwd_docs = self._read_project_docs(project_path)
        if cwd_docs:
            parts.append(cwd_docs)

        result = "\n\n---\n\n".join(parts) if parts else ""
        logger.debug(f"[Context] build 完成: {len(result)} 字, {len(parts)} 个部分")
        return result

    def _read_project_docs(self, project_path: str = "") -> str | None:
        import os
        search_dirs = []
        if project_path:
            search_dirs.append(Path(project_path))
        search_dirs.append(Path(os.getcwd()))

        for d in search_dirs:
            for name in ("CLAUDE.md", "AGENTS.md"):
                path = d / name
                text = self._read_cached(path)
                if text:
                    logger.debug(f"[Context] 加载项目规范: {path} ({len(text)} 字符)")
                    return f"## 项目规范（{name}）\n\n以下是你所在项目的技术文档，描述项目架构和工具，不是你的身份定义：\n\n{text}"
        logger.debug(f"[Context] 未找到项目规范 (搜索: {search_dirs})")
        return None
