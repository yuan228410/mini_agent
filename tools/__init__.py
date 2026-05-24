"""工具注册与分发"""
import json

from tools import run_command, web_fetch

_ALL_TOOLS = [run_command, web_fetch]
_BY_NAME: dict[str, object] = {
    m.definition["function"]["name"]: m for m in _ALL_TOOLS
}


def register(skill_loader) -> None:
    """注入 skill_loader 依赖，注册技能相关工具"""
    from tools import list_skills, load_skill

    list_skills._loader = skill_loader
    load_skill._loader = skill_loader
    _ALL_TOOLS.extend([list_skills, load_skill])
    _BY_NAME.update({
        m.definition["function"]["name"]: m for m in [list_skills, load_skill]
    })


def get_definitions() -> list[dict]:
    """返回所有工具的 OpenAI 定义列表"""
    return [m.definition for m in _ALL_TOOLS]


def dispatch(name: str, args: dict) -> str | None:
    """根据工具名分发执行，返回结果或 None"""
    mod = _BY_NAME.get(name)
    return mod.execute(args) if mod else None


def handle_tool_calls(msg: dict, messages: list[dict]) -> None:
    """处理消息中的 tool_calls，执行并将结果追加到 messages"""
    for tool_call in msg["tool_calls"]:
        func = tool_call["function"]
        args = json.loads(func["arguments"]) if func["arguments"] else {}

        output = dispatch(func["name"], args)
        if output is None:
            continue

        messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
        messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": output})