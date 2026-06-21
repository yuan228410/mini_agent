"""Skill management use cases shared by UI adapters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..skills import SkillLoader
from ..tools.install_skill import install_skill_with_loader


@dataclass(frozen=True, slots=True)
class SkillServiceError(Exception):
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


def list_skills(loader: SkillLoader) -> dict[str, Any]:
    """Return summary information for all loaded skills."""

    return {
        "skills": [
            {
                "name": name,
                "description": skill["meta"].get("description", ""),
                "tags": skill["meta"].get("tags", ""),
                "tier": skill.get("tier", ""),
            }
            for name, skill in loader.skills.items()
        ]
    }


def get_skill_info(loader: SkillLoader, name: str) -> dict[str, Any]:
    """Return full skill metadata and body."""

    skill = _require_skill(loader, name)
    meta = skill["meta"]
    return {
        "name": name,
        "description": meta.get("description", ""),
        "tags": meta.get("tags", ""),
        "tier": skill.get("tier", "global"),
        "path": skill["path"],
        "content": skill["body"],
    }


def load_skill(loader: SkillLoader, name: str) -> dict[str, Any]:
    """Return the skill body payload used by load endpoints/tools."""

    skill = _require_skill(loader, name)
    return {
        "ok": True,
        "name": name,
        "content": skill["body"],
        "meta": skill["meta"],
    }


def install_skill(loader: SkillLoader, source: str, level: str = "global") -> dict[str, Any]:
    """Install a skill from a URL or local archive path."""

    result = install_skill_with_loader(loader, {"source": source, "level": level})
    if result.startswith("Error:") or "失败" in result:
        return {"ok": False, "error": result}
    return {"ok": True, "message": result}


def create_skill_template(loader: SkillLoader, name: str, level: str = "global") -> dict[str, Any]:
    """Create a writable skill template in the requested tier."""

    target_dir = loader.get_tier_dir(level)
    if not target_dir:
        raise SkillServiceError(f"层级 '{level}' 未配置", status_code=400)

    skill_dir = target_dir / name
    if skill_dir.exists():
        raise SkillServiceError(f"技能 '{name}' 已存在于 {level} 层级", status_code=400)

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(_skill_template(name), encoding="utf-8")
    loader.reload()

    return {
        "ok": True,
        "message": f"技能模板 '{name}' 已创建",
        "path": str(skill_dir),
    }


def delete_skill(loader: SkillLoader, name: str, level: str = "") -> dict[str, Any]:
    """Delete a skill from a selected tier or the currently active tier."""

    result = loader.delete_skill_at(name, level) if level else loader.delete_skill(name)
    if result.startswith("Error:"):
        return {"ok": False, "error": result}
    return {"ok": True, "message": result}


def _require_skill(loader: SkillLoader, name: str) -> dict[str, Any]:
    skill = loader.skills.get(name)
    if not skill:
        raise SkillServiceError(f"技能 '{name}' 不存在", status_code=404)
    return skill


def _skill_template(name: str) -> str:
    return f"""---
name: {name}
description: 技能描述（请修改）
tags: 标签1,标签2
---

# {name}

技能内容（请修改）

## 使用场景

- 场景1
- 场景2

## 步骤

1. 步骤1
2. 步骤2
"""
