"""技能列表工具"""
_loader = None

definition = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": "列出所有可用的技能(skill)，返回技能名称和描述",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}


def execute(args: dict) -> str:
    return _loader.get_descriptions()