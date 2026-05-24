"""技能加载工具"""
from logger import logger

_loader = None

definition = {
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


def execute(args: dict) -> str:
    name = args["name"]
    logger.info(f"[加载技能] {name}")
    return _loader.get_content(name)