"""Agent 执行器 — 可复用的 LLM 对话循环"""


def _filter_tools(tool_names: list[str]) -> list[dict]:
    from tools import get_definitions
    return [d for d in get_definitions() if d["function"]["name"] in tool_names]


def run_agent(messages: list[dict], *, max_turns: int = 10, tool_names: list[str] | None = None) -> str | None:
    """运行 agent 循环直到产出最终回复或超出轮次。

    Args:
        messages: 初始消息列表（含 system prompt）
        max_turns: 最大 LLM 调用轮次
        tool_names: 工具名称白名单，None 表示全部工具

    Returns:
        最终的文本回复，或 None（超出轮次/出错）
    """
    from llm import chat as llm_chat
    from tools import handle_tool_calls

    tools = _filter_tools(tool_names) if tool_names else True

    for _ in range(max_turns):
        msg = llm_chat(messages, tools=tools)

        if not msg:
            return None

        if "tool_calls" not in msg:
            return msg.get("content")

        handle_tool_calls(msg, messages)

    return None