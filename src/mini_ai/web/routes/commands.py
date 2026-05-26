"""斜杠命令列表接口"""
from fastapi import APIRouter

from ...config import AVAILABLE_MODELS
from ...tools import dispatch

router = APIRouter()

_WEB_COMMANDS = [
    {"name": "/clear", "desc": "清空当前会话消息", "has_arg": False},
    {"name": "/compact", "desc": "手动触发对话压缩", "has_arg": False},
    {"name": "/genskill", "desc": "从对话生成技能", "has_arg": True, "arg_name": "技能名称"},
    {"name": "/skill", "desc": "使用指定技能执行任务", "has_arg": True, "arg_name": "技能名称"},
    {"name": "/model", "desc": "切换模型", "has_arg": True, "arg_name": "模型名称"},
    {"name": "/thinking", "desc": "设置思考展示模式", "has_arg": True, "arg_name": "collapsed/expanded/hidden"},
]

@router.get("/commands")
async def list_commands():
    commands = []
    for cmd in _WEB_COMMANDS:
        entry = dict(cmd)
        if cmd["name"] == "/model":
            entry["options"] = [{"value": n} for n in AVAILABLE_MODELS]
        elif cmd["name"] == "/skill":
            try:
                skills_text = dispatch("list_skills", {})
                entry["options"] = [{"value": s.strip()} for s in skills_text.strip().split("\n") if s.strip() and not s.strip().startswith("(")]
            except Exception:
                entry["options"] = []
        else:
            entry["options"] = []
        commands.append(entry)
    return {"commands": commands}
