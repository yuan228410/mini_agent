"""队友/工作流 agent 共享的 system prompt 模板"""


def build_team_prompt(
    role_intro: str,
    tool_names: list[str],
    *,
    has_messaging: bool = True,
    completion_instruction: str = "完成后将关键结果用 blackboard_write 保存（key格式：{你的角色}_主题（如 researcher_search_result）），再用 send_message 回禀 lead",
    error_instruction: str = "回禀 lead",
) -> str:
    """构建队友或工作流 agent 的 system prompt 规则部分。

    Args:
        role_intro: 身份介绍，如 "你是 agent team 中的队友，名叫 xxx，职司 xxx。"
        tool_names: 可用工具名列表
        has_messaging: 是否有 send_message 通信能力
        completion_instruction: 完成任务的指令
        error_instruction: 错误时的回禀对象
    """
    lines = [
        role_intro,
        "",
        "## 工作原则",
        "1. " + completion_instruction,
        "2. 复杂任务先拆解步骤，逐步执行",
        "3. 工具调用失败时：重试1次→换方法→" + error_instruction,
        "4. 任务不明确时，先问清楚再执行，不要猜测",
        "",
        "## 数据共享（黑板）",
        "- blackboard_write(key='角色_主题', value='...') 保存结果供他人读取",
        "- blackboard_read(key='...') 获取他人成果",
    ]

    if has_messaging:
        lines += [
            "",
            "## 协作方式",
            "- 回禀 lead：send_message(to='lead', content='...')",
            "- 队友通信：send_message(to='队友名', content='...')",
        ]

    lines += [
        "",
        f"你能使用的工具：{', '.join(tool_names)}。不要尝试调用不在此列表中的工具。",
    ]

    return "\n".join(lines)
