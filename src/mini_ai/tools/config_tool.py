"""配置读取与修改工具"""
import json

from ..config import _raw, _config_path, AVAILABLE_MODELS, MODEL_CONFIG, MCP
from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "config",
        "description": "读取或修改 mini-ai 配置。action=read 读取指定路径的配置值；action=write 修改配置并持久化到 config.yaml；action=list 返回配置结构概览。修改后需重启生效的项会标注。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "list"],
                    "description": "read=读取配置值, write=修改配置值, list=列出配置结构"
                },
                "path": {
                    "type": "string",
                    "description": "配置路径，用点号分隔，如 'compactor.keep_recent'、'mcp.servers'、'models.deepseek.api_key'"
                },
                "value": {
                    "description": "要设置的新值（write 时必填），支持字符串、数字、布尔、对象、数组"
                }
            },
            "required": ["action"],
        },
    },
}

def _resolve(obj, parts):
    for p in parts:
        if isinstance(obj, dict):
            if p not in obj:
                return None, False
            obj = obj[p]
        elif isinstance(obj, list):
            try:
                idx = int(p)
                obj = obj[idx]
            except (ValueError, IndexError):
                return None, False
        else:
            return None, False
    return obj, True

def _set(obj, parts, value):
    for p in parts[:-1]:
        if isinstance(obj, dict):
            if p not in obj:
                obj[p] = {}
            obj = obj[p]
        elif isinstance(obj, list):
            obj = obj[int(p)]
    last = parts[-1]
    if isinstance(obj, dict):
        obj[last] = value
    elif isinstance(obj, list):
        obj[int(last)] = value

_CONFIG_STRUCTURE = """mini-ai 配置结构:
- active_model: 当前激活模型名
- models: 模型定义字典（每个模型含 api_url, api_key, model, context_length, api_mode, headers, temperature, max_tokens, top_p, reasoning_effort）
- streaming: 是否流式输出
- timeouts: 超时配置（llm, llm_retries, llm_retry_delay, teammate_recv, lead_wait, web_fetch）
- compactor: 压缩配置（context_usage_threshold, keep_recent, char_threshold）
- teammate: 队友配置（max_teammates, max_turns, base_tools）
- tool: 工具配置（max_result_chars）
- thinking: 思考配置（enabled, budget_tokens）
- display: 显示配置（thinking_mode, tool_detail）
- web: Web 配置（history_limit）
- plan: 计划模式配置（approval）
- mcp: MCP 配置（enabled, connect_timeout, execute_timeout, sse_read_timeout, servers）
- skill_paths: 额外技能搜索路径列表
- runner: 运行器配置（context_usage_limit）

修改 models/mcp/plan 后需重启生效。修改 active_model 可通过 /model 命令即时切换。"""


def _build_self_overview() -> str:
    try:
        from ..config import _raw, AVAILABLE_MODELS, MODEL_CONFIG, MCP, STREAMING, THINKING, DISPLAY, PLAN, COMPACTOR, DATA_DIR, PACKAGE_DIR
        from .. import __version__
        from ..tools import get_definitions
    except Exception:
        return ""

    active = _raw.get("active_model", "?")
    model_name = MODEL_CONFIG.get("model", "?")
    ctx_len = MODEL_CONFIG.get("context_length", 128000)
    api_mode = MODEL_CONFIG.get("api_mode", "openai")

    tools = get_definitions()
    tool_names = [t["function"]["name"] for t in tools]
    mcp_tools = [n for n in tool_names if n.startswith("mcp_")]
    builtin_tools = [n for n in tool_names if not n.startswith("mcp_")]

    lines = [
        f"mini-ai v{__version__}，智能对话 Agent",
        f"当前模型: {active} ({model_name}, {api_mode}, context_length={ctx_len})",
        f"可用模型: {', '.join(AVAILABLE_MODELS)}",
        f"流式输出: {'是' if STREAMING else '否'}",
        f"思考模式: {'启用' if THINKING.get('enabled') else '禁用'} (budget={THINKING.get('budget_tokens', 10000)})，显示: {DISPLAY.get('thinking_mode', 'collapsed')} (collapsed/expanded/hidden)",
        f"计划模式审批: {'需要' if PLAN.get('approval', True) else '自动执行'}",
        f"压缩阈值: keep_recent={COMPACTOR.get('keep_recent', 50)}, char_threshold={COMPACTOR.get('char_threshold', 20000)}",
    ]
    if MCP.get("enabled"):
        lines.append(f"MCP: 启用 ({len(mcp_tools)} 个工具)")
    else:
        lines.append("MCP: 禁用")
    lines.append(f"内置工具 ({len(builtin_tools)}): {', '.join(builtin_tools)}")
    if mcp_tools:
        lines.append(f"MCP 工具 ({len(mcp_tools)}): {', '.join(mcp_tools)}")
    lines.append(f"配置文件: {DATA_DIR}/config.yaml")
    lines.append(f"源码目录: {PACKAGE_DIR}")
    lines.append(f"项目文档: {PACKAGE_DIR.parent.parent}/docs/, {PACKAGE_DIR.parent.parent}/WEB.md")
    lines.append(f"配置示例: {PACKAGE_DIR}/config.example.yaml（包含所有配置项及详细说明）")
    lines.append("可用 read_file 读取源码/文档，可用 config 工具读取/修改配置")
    return "\n".join(lines)

def execute(args: dict) -> str:
    action = args.get("action", "read")
    path = args.get("path", "")
    value = args.get("value")

    if action == "list":
        return _build_self_overview() + "\n\n" + _CONFIG_STRUCTURE

    if action == "read":
        if not path:
            keys = list(_raw.keys())
            return f"配置顶层键: {', '.join(keys)}"
        parts = path.split(".")
        val, found = _resolve(_raw, parts)
        if not found:
            return f"配置路径 '{path}' 不存在"
        if isinstance(val, dict) and "api_key" in val:
            safe = {k: ("***" if k == "api_key" else v) for k, v in val.items()}
            return json.dumps(safe, ensure_ascii=False, indent=2)
        if isinstance(val, dict):
            return json.dumps(val, ensure_ascii=False, indent=2)
        return str(val)

    if action == "write":
        if not path:
            return "write 需要指定 path"
        if value is None:
            return "write 需要指定 value"
        parts = path.split(".")
        _set(_raw, parts, value)
        import yaml
        with open(_config_path, "w", encoding="utf-8") as f:
            yaml.dump(_raw, f, default_flow_style=False, allow_unicode=True)
        logger.info(f"[配置] 已修改 {path} = {value}")
        needs_restart = any(parts[0] == k for k in ("models", "mcp", "plan", "compactor", "thinking", "timeouts", "tool", "runner", "web", "display", "skill_paths", "streaming"))
        hint = "（需重启 mini-ai 生效）" if needs_restart else "（即时生效）"
        if parts[0] == "active_model" and isinstance(value, str):
            hint = "（可用 /model 命令即时切换）"
        return f"已修改 {path} = {json.dumps(value, ensure_ascii=False)} {hint}"

    return f"未知 action: {action}"
