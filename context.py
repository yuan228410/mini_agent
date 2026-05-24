"""上下文组装器：从多个来源构建系统提示词"""
from pathlib import Path


class ContextBuilder:
    """按优先级组装系统提示词。

    组装顺序：
      1. 核心身份 (SOUL.md)
      2. 长期记忆 (MemoryStore)
      3. 用户画像 (MemoryStore)
      4. 技能列表 (SkillLoader)
      5. 系统指令 (SYSTEM_PROMPT 模板)
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.character_dir = self.project_dir / "character"

    def build(self, memory_store=None, skill_loader=None) -> str:
        parts = []

        soul = self._read_doc("SOUL.md")
        if soul:
            parts.append(soul)

        if memory_store:
            if memory_store.has_memory():
                parts.append(f"## 长期记忆\n\n{memory_store.read_memory()}")

            if memory_store.has_user():
                parts.append(f"## 用户画像\n\n{memory_store.read_user()}")

        if skill_loader:
            skills_text = skill_loader.get_descriptions()
            if skills_text and skills_text != "(no skills available)":
                parts.append(f"## 可用技能\n\n{skills_text}")

        # 系统运行指令放最后，优先级最低
        rules = self._read_doc("RULES.md")
        if rules:
            parts.append(rules)

        return "\n\n---\n\n".join(parts) if parts else ""

    def _read_doc(self, name: str) -> str | None:
        path = self.character_dir / name
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return None