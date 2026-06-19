"""技能删除工具"""
from ..core.runtime_types import ToolArgs, ToolDefinition
import contextvars

from ..logger import logger

_loader_var = contextvars.ContextVar("skill_loader", default=None)
_loader = None


def configure(loader=None):
    global _loader
    if loader is not None:
        _loader = loader
        _loader_var.set(loader)


def _get_loader():
    return _loader_var.get() or _loader


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


def execute(args: ToolArgs) -> str:
    loader = _get_loader()
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
