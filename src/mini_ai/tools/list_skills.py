"""技能列表工具"""
from ..core.runtime_types import ToolArgs, ToolDefinition
import contextvars

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
        "name": "list_skills",
        "description": "列出所有可用技能。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}


def execute(args: ToolArgs) -> str:
    loader = _get_loader()
    if not loader:
        return "Error: 技能加载器未配置"
    return loader.get_descriptions()
