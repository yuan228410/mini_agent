"""技能删除工具"""
from ..core.runtime_types import ToolArgs, ToolDefinition

from ..logger import logger


definition: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "delete_skill",
        "description": "删除技能。不指定 level 则从 workspace→user→global 逐级查找。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称"},
                "level": {"type": "string", "enum": ["global", "user", "workspace"], "description": "删除层级"},
            },
            "required": ["name"],
        },
    },
}


def delete_skill_with_loader(loader, args: ToolArgs) -> str:
    if not loader:
        return "Error: 技能加载器未配置"
    name = args.get("name", "")
    level = args.get("level")

    if level:
        return loader.delete_skill_at(name, level)

    skill = loader.skills.get(name)
    if not skill:
        return f"Error: 技能 '{name}' 不存在"

    tier = skill.get("tier", "")
    logger.info(f"[删除技能] {name} 当前层级: {tier}，直接删除")
    return loader.delete_skill(name)


def execute(args: ToolArgs) -> str:
    return "Error: 技能加载器未配置"
