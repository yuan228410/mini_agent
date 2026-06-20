"""技能列表工具"""
from ..core.runtime_types import ToolArgs, ToolDefinition


definition: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": "列出所有可用技能。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}


def list_skills_with_loader(loader, args: ToolArgs) -> str:
    if not loader:
        return "Error: 技能加载器未配置"
    return loader.get_descriptions()


def execute(args: ToolArgs) -> str:
    return "Error: 技能加载器未配置"
