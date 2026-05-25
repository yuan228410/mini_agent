"""技能加载器：扫描 skills/ 目录，加载 SKILL.md 文件"""
import re
from pathlib import Path

import yaml


class SkillLoader:
    """加载并管理 skills/ 目录下所有 SKILL.md 技能文件。

    每个技能目录结构：
        skills/<name>/SKILL.md
            ---
            name: skill-name
            description: 简短描述
            tags: tag1,tag2
            ---
            技能正文...
    """

    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)
        self.skills: dict[str, dict] = {}
        self._load_all()

    def _load_all(self):
        if not self.skills_dir.exists():
            return
        for f in sorted(self.skills_dir.rglob("SKILL.md")):
            text = f.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body, "path": str(f)}

    def _parse_frontmatter(self, text: str) -> tuple[dict, str]:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        return meta, match.group(2).strip()

    def get_descriptions(self) -> str:
        """返回所有技能的列表描述，用于注入 system prompt"""
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "No description")
            tags = skill["meta"].get("tags", "")
            line = f"  - {name}: {desc}"
            if tags:
                line += f" [{tags}]"
            lines.append(line)
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        """加载指定技能的完整内容"""
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        meta = skill["meta"]
        header = f'技能: {name}'
        if meta.get("description"):
            header += f'\n描述: {meta["description"]}'
        if meta.get("tags"):
            header += f'\n标签: {meta["tags"]}'
        return f'<skill name="{name}">\n{header}\n\n{skill["body"]}\n</skill>'