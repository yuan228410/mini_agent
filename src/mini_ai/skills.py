"""技能加载器：三层级技能扫描（global → user → workspace，后覆盖前）"""
import re
import shutil
from pathlib import Path

import yaml

from .logger import logger


class SkillLoader:
    """加载并管理多个路径下的 SKILL.md 技能文件。

    三层级目录结构（优先级从低到高）：
        extra_paths: 只读补充路径（最低优先级）
        global:      skills_dir / <name> / SKILL.md
        user:        user_skills_dir / <name> / SKILL.md
        workspace:   workspace_skills_dir / <name> / SKILL.md

    同名技能后者覆盖前者（last-wins）。
    """

    def __init__(self, skills_dir: Path, extra_paths: list[Path] | None = None,
                 user_skills_dir: Path | None = None,
                 workspace_skills_dir: Path | None = None):
        self.skills_dir = Path(skills_dir)
        self.extra_paths = [Path(p) for p in (extra_paths or [])]
        self._tier_paths: list[tuple[str, Path]] = [("global", self.skills_dir)]
        seen = {self.skills_dir.resolve()}
        if user_skills_dir is not None:
            p = Path(user_skills_dir).resolve()
            if p not in seen:
                self._tier_paths.append(("user", Path(user_skills_dir)))
                seen.add(p)
        if workspace_skills_dir is not None:
            p = Path(workspace_skills_dir).resolve()
            if p not in seen:
                self._tier_paths.append(("workspace", Path(workspace_skills_dir)))
                seen.add(p)
        self.skills: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        """Reload skills from all configured tiers."""
        self.skills.clear()
        for path in self.extra_paths:
            self._load_from_dir("extra", path)
        for tier, path in self._tier_paths:
            self._load_from_dir(tier, path)

    def tier_paths(self) -> list[tuple[str, Path]]:
        """Return configured writable skill tiers in precedence order."""
        return list(self._tier_paths)

    def _load_from_dir(self, tier: str, path: Path):
        if not path.exists():
            return
        for f in sorted(path.glob("*/SKILL.md")):
            text = f.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body, "path": str(f), "tier": tier}

    def _parse_frontmatter(self, text: str) -> tuple[dict, str]:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        return meta, match.group(2).strip()

    def get_tier_dir(self, level: str) -> Path | None:
        for tier, path in self._tier_paths:
            if tier == level:
                return path
        return None

    def delete_skill(self, name: str) -> str:
        skill = self.skills.get(name)
        if not skill:
            return f"Error: 技能 '{name}' 不存在"
        tier = skill.get("tier", "global")
        if tier == "extra":
            return f"Error: 技能 '{name}' 位于扩展路径（只读），不可删除"
        skill_dir = Path(skill["path"]).parent
        try:
            shutil.rmtree(skill_dir)
            logger.info(f"[删除技能] {name} ({tier}) → {skill_dir}")
            self.reload()
            if name in self.skills:
                return f"技能 '{name}' 的 {tier} 级副本已删除（现在使用 {self.skills[name]['tier']} 级版本）"
            return f"技能 '{name}' 已删除"
        except Exception as e:
            logger.error(f"[删除技能] {name} 删除失败: {e}")
            return f"Error: 删除技能 '{name}' 失败 - {e}"

    def delete_skill_at(self, name: str, level: str) -> str:
        if level == "extra":
            return "Error: 扩展路径为只读，不可删除"
        skill = self.skills.get(name)
        if not skill:
            return f"Error: 技能 '{name}' 不存在"
        tier = skill.get("tier", "global")
        if tier == "extra":
            return f"Error: 技能 '{name}' 当前仅存在于扩展路径（只读），在 '{level}' 层级不存在。如需安装请使用 install_skill level={level}"
        if tier == level:
            return self.delete_skill(name)
        target_dir = self.get_tier_dir(level)
        if not target_dir:
            return f"Error: 层级 '{level}' 未配置，无法删除"
        skill_dir = target_dir / name
        if not skill_dir.exists():
            return f"Error: 技能 '{name}' 在 {level} 层级不存在（当前位于 {tier} 层级）"
        try:
            shutil.rmtree(skill_dir)
            logger.info(f"[删除技能] {name} ({level}) → {skill_dir}")
            self.reload()
            if name in self.skills:
                return f"技能 '{name}' 的 {level} 级副本已删除（当前使用 {self.skills[name]['tier']} 级版本）"
            return f"技能 '{name}' 的 {level} 级副本已删除"
        except Exception as e:
            logger.error(f"[删除技能] {name} ({level}) 删除失败: {e}")
            return f"Error: 删除技能 '{name}' ({level}) 失败 - {e}"

    def get_descriptions(self) -> str:
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "No description")
            tags = skill["meta"].get("tags", "")
            tier = skill.get("tier", "")
            line = f"  - {name}: {desc}"
            if tags:
                line += f" [{tags}]"
            if tier and tier != "extra":
                line += f" ({tier})"
            lines.append(line)
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        meta = skill["meta"]
        header = f'技能: {name}'
        if meta.get("description"):
            header += f'\n描述: {meta["description"]}'
        if meta.get("tags"):
            header += f'\n标签: {meta["tags"]}'
        tier = skill.get("tier", "")
        if tier:
            header += f'\n层级: {tier}'
        skill_dir = str(Path(skill["path"]).parent)
        return f'<skill name="{name}" dir="{skill_dir}">\n{header}\n\n{skill["body"]}\n</skill>\n\n技能目录: {skill_dir}，执行脚本时请使用绝对路径，如: python3 {skill_dir}/scripts/xxx.py'
