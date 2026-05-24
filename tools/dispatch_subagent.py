"""子代理调度工具"""
from logger import logger
from subagents import SubagentLoader

_loader: SubagentLoader | None = None

definition = {
    "type": "function",
    "function": {
        "name": "dispatch_subagent",
        "description": (
            "派遣子代理执行独立任务。适用场景：并行搜索多个信息源、"
            "独立完成代码修改、分析单一文件。子代理独立运行，完成后返回结果摘要。\n"
            "可用子代理类型：\n"
            "{subagent_list}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "子代理类型"},
                "task": {"type": "string", "description": "委派给子代理的任务描述"},
            },
            "required": ["type", "task"],
        },
    }
}


def execute(args: dict) -> str:
    from runner import run_agent

    global definition
    spec = _loader.get(args["type"])
    if not spec:
        names = ", ".join(_loader.specs.keys())
        return f"未知子代理类型 '{args['type']}'，可用：{names}"

    task = args["task"]
    logger.info(f"[派遣] {spec['name']}: {task[:50]}")

    messages = [
        {"role": "system", "content": spec["system_prompt"]},
        {"role": "user", "content": task},
    ]

    result = run_agent(messages, max_turns=spec["max_turns"], tool_names=spec["tool_names"])
    return result or f"[{spec['name']}] 超出轮次限制或执行失败"


def build_definition(subagent_list: str) -> dict:
    """用最新的子代理列表动态更新工具描述"""
    d = definition.copy()
    d["function"] = d["function"].copy()
    d["function"]["description"] = d["function"]["description"].format(
        subagent_list=subagent_list
    )
    return d