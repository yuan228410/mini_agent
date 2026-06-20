"""技能加载工具"""
from ..core.runtime_types import ToolArgs, ToolDefinition

from ..logger import logger


definition: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "加载指定技能的完整内容",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称"}
            },
            "required": ["name"]
        }
    }
}


def load_skill_with_loader(loader, args: ToolArgs) -> str:
    if not loader:
        return "Error: 技能加载器未配置"
    name = args.get("name", "")
    logger.info(f"[加载技能] {name}")
    return loader.get_content(name)


def execute(args: ToolArgs) -> str:
    return "Error: 技能加载器未配置"
