"""斜杠命令列表接口"""
from fastapi import APIRouter

from ...config import AVAILABLE_MODELS
from ...tools import dispatch

router = APIRouter()

_WEB_COMMANDS = [
    {"name": "/plan", "desc": "进入计划模式（只规划不执行）", "has_arg": False},
    {"name": "/act", "desc": "切换到执行模式", "has_arg": False},
    {"name": "/clear", "desc": "清空当前会话消息", "has_arg": False},
    {"name": "/compact", "desc": "手动触发对话压缩", "has_arg": False},
    {"name": "/genskill", "desc": "从对话生成技能", "has_arg": True, "arg_name": "技能名称"},
    {"name": "/skill", "desc": "使用指定技能执行任务", "has_arg": True, "arg_name": "技能名称"},
    {"name": "/model", "desc": "切换模型", "has_arg": True, "arg_name": "模型名称"},
    {"name": "/thinking", "desc": "设置思考展示模式", "has_arg": True, "arg_name": "collapsed/expanded/hidden"},
    {"name": "/workspace", "desc": "列出所有工作空间", "has_arg": False},
    {"name": "/workspace new", "desc": "创建新工作空间", "has_arg": True, "arg_name": "名称 [路径]"},
    {"name": "/workspace add", "desc": "添加现有文件夹为工作空间", "has_arg": True, "arg_name": "路径"},
    {"name": "/workspace remove", "desc": "移除工作空间（保留数据）", "has_arg": True, "arg_name": "名称"},
    {"name": "/workspace delete", "desc": "删除工作空间（含数据）", "has_arg": True, "arg_name": "名称"},
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
