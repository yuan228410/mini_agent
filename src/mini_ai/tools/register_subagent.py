"""动态注册子代理工具 — 对话中创建新的子代理类型"""
import json
from pathlib import Path

from ..config import PACKAGE_DIR
from ..logger import logger

_loader = None
_subagents_dir = None


def configure(loader=None):
    global _loader, _subagents_dir
    if loader is not None:
        _loader = loader
        _subagents_dir = loader.subagents_dir


definition = {
    "type": "function",
    "function": {
        "name": "register_subagent",
        "description": (
            "创建并注册一个新的子代理类型。子代理是可独立派遣执行任务的一次性 Agent。"
            "创建后立即可用 dispatch_subagent 调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "子代理名称，英文字母和连字符，如 code-reviewer、data-analyzer",
                },
                "description": {
                    "type": "string",
                    "description": "简短描述，用于在 dispatch_subagent 工具列表中展示",
                },
                "prompt": {
                    "type": "string",
                    "description": "子代理的系统提示词，定义其职责、行为规范和可用工具。包含工具列表时用 tools 参数指定",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "允许子代理使用的工具列表，如 ['run_command', 'web_fetch']。留空则使用全部工具",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "最大工具调用轮次，默认 10",
                },
            },
            "required": ["name", "description", "prompt"],
        },
    },
}


def _rebuild_dispatch_definition():
    """通知 dispatch_subagent 重建带新列表的工具定义"""
    from ..tools import dispatch_subagent as dsa
    subagent_list = _loader.list_specs()
    new_def = dsa.build_definition(subagent_list)
    dsa._definition = new_def
    dsa.definition = new_def
    logger.info(f"[注册子代理] 已刷新 dispatch_subagent 工具定义")


def execute(args: dict) -> str:
    name = args.get("name", "").strip()
    description = args.get("description", "").strip()
    prompt = args.get("prompt", "").strip()
    tools = args.get("tools") or []
    max_turns = args.get("max_turns", 10)

    if not name:
        return "Error: name 不能为空"
    if not description:
        return "Error: description 不能为空"
    if not prompt:
        return "Error: prompt 不能为空"

    if _loader and name in _loader.specs:
        return f"Error: 子代理 '{name}' 已存在，如需覆盖请先删除"

    try:
        max_turns = int(max_turns)
    except (TypeError, ValueError):
        max_turns = 10
    if max_turns <= 0:
        max_turns = 10

    tools_str = ", ".join(str(t) for t in tools) if tools else ""

    frontmatter = []
    frontmatter.append(f"name: {name}")
    frontmatter.append(f"description: {description}")
    if tools_str:
        frontmatter.append(f"tools: {tools_str}")
    frontmatter.append(f"max_turns: {max_turns}")

    md_content = "---\n" + "\n".join(frontmatter) + "\n---\n\n" + prompt + "\n"

    if _subagents_dir:
        dest = Path(_subagents_dir) / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md_content, encoding="utf-8")
        logger.info(f"[注册子代理] 已写入 {dest}")

    if _loader:
        _loader._load_all()
        _rebuild_dispatch_definition()

    lines = [
        f"子代理 '{name}' 已创建并注册",
        f"描述: {description}",
    ]
    if tools_str:
        lines.append(f"工具: {tools_str}")
    lines.append(f"轮次上限: {max_turns}")
    lines.append("")
    lines.append("现在可以通过 dispatch_subagent 派遣使用。")
    return "\n".join(lines)