"""技能加载工具"""
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


def execute(args: ToolArgs) -> str:
    loader = _get_loader()
    if not loader:
        return "Error: 技能加载器未配置"
    name = args.get("name", "")
    logger.info(f"[加载技能] {name}")
    return loader.get_content(name)
