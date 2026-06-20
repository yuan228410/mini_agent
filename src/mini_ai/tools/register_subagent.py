"""动态注册子代理工具 — 对话中创建新的子代理类型"""
from ..core.runtime_types import ToolArgs, ToolDefinition
from pathlib import Path

from ..logger import logger


definition: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "register_subagent",
        "description": "创建新的子代理类型。创建后立即可用 dispatch_subagent 调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "子代理名称"},
                "description": {"type": "string", "description": "简短描述"},
                "prompt": {"type": "string", "description": "系统提示词"},
                "tools": {"type": "array", "items": {"type": "string"}, "description": "允许使用的工具列表"},
                "max_turns": {"type": "integer", "description": "最大轮次，默认 10"},
            },
            "required": ["name", "description", "prompt"],
        },
    },
}


def execute_with_context(loader, args: ToolArgs, *, refresh_dispatch=None) -> str:
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

    if not loader:
        return "Error: 子代理加载器未配置"

    if name in loader.specs:
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

    subagents_dir = getattr(loader, "subagents_dir", None)
    if subagents_dir:
        dest = Path(subagents_dir) / f"{name}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md_content, encoding="utf-8")
        logger.info(f"[注册子代理] 已写入 {dest}")

    loader._load_all()
    if refresh_dispatch:
        refresh_dispatch()
        logger.info("[注册子代理] 已刷新 dispatch_subagent 工具定义")

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


def execute(args: ToolArgs) -> str:
    return "Error: 子代理加载器未配置"
