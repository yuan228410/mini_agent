from __future__ import annotations

from enum import Enum

from ..tools.metadata import metadata_for


class ToolPolicy(str, Enum):
    PLAN_DISCUSSION = "plan_discussion"
    PLAN_READONLY = "plan_readonly"
    EXECUTION = "execution"


DEFAULT_READONLY_TOOLS = {
    "read_file",
    "read_image",
    "search_files",
    "list_dir",
    "web_fetch",
    "list_skills",
    "load_skill",
    "recall",
    "search_history",
}


def filter_tools(tools: list[dict] | None, policy: ToolPolicy | str) -> list[dict]:
    if not tools:
        return []
    policy = ToolPolicy(policy)
    if policy == ToolPolicy.EXECUTION:
        return tools
    if policy == ToolPolicy.PLAN_DISCUSSION:
        return []

    allowed = DEFAULT_READONLY_TOOLS
    result: list[dict] = []
    for tool in tools:
        name = tool.get("function", {}).get("name", "")
        meta = tool.get("metadata") or {}
        if meta.get("allowed_in_plan") or metadata_for(name).allowed_in_plan or name in allowed:
            result.append(tool)
    return result
