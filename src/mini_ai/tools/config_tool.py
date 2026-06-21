"""配置读取与修改工具"""
from ..core.runtime_types import ToolArgs, ToolDefinition
import importlib
import json

from ..logger import logger

definition: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "config",
        "description": "读取或修改配置。action: read/write/list/reload。修改后部分项需重启。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "list", "reload"],
                    "description": "read/write/list/reload"
                },
                "path": {"type": "string", "description": "配置路径，如 'models.deepseek.api_key'"},
                "value": {"description": "要设置的值（write 时必填）"}
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
- compactor: 压缩配置（context_usage_threshold, keep_recent, keep_budget_ratio, early_compact_ratio, max_cached_summaries）
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


def _config_module():
    return importlib.import_module("mini_ai.config")


def _build_self_overview(registry=None) -> str:
    try:
        cfg = _config_module()
        from .. import __version__
    except Exception:
        return ""

    active = cfg._raw.get("active_model", "?")
    model_name = cfg.MODEL_CONFIG.get("model", "?")
    ctx_len = cfg.MODEL_CONFIG.get("context_length", 256000)
    api_mode = cfg.MODEL_CONFIG.get("api_mode", "openai")

    tools = registry.get_definitions() if registry is not None else []
    tool_names = [t["function"]["name"] for t in tools]
    mcp_tools = [n for n in tool_names if n.startswith("mcp_")]
    builtin_tools = [n for n in tool_names if not n.startswith("mcp_")]

    lines = [
        f"mini-ai v{__version__}，智能对话 Agent",
        f"当前模型: {active} ({model_name}, {api_mode}, context_length={ctx_len})",
        f"可用模型: {', '.join(cfg.AVAILABLE_MODELS)}",
        f"流式输出: {'是' if cfg.STREAMING else '否'}",
        f"思考模式: {'启用' if cfg.THINKING.get('enabled') else '禁用'} (budget={cfg.THINKING.get('budget_tokens', 10000)})，显示: {cfg.DISPLAY.get('thinking_mode', 'collapsed')} (collapsed/expanded/hidden)",
        f"计划模式审批: {'需要' if cfg.PLAN.get('approval', True) else '自动执行'}",
        f"压缩: keep_recent={cfg.COMPACTOR.get('keep_recent', 50)} budget_ratio={cfg.COMPACTOR.get('keep_budget_ratio', 0.2)} early_ratio={cfg.COMPACTOR.get('early_compact_ratio', 0.85)} max_cache={cfg.COMPACTOR.get('max_cached_summaries', 200)}",
    ]
    if cfg.MCP.get("enabled"):
        lines.append(f"MCP: 启用 ({len(mcp_tools)} 个工具)")
    else:
        lines.append("MCP: 禁用")
    lines.append(f"内置工具 ({len(builtin_tools)}): {', '.join(builtin_tools)}")
    if mcp_tools:
        lines.append(f"MCP 工具 ({len(mcp_tools)}): {', '.join(mcp_tools)}")
    lines.append(f"配置文件: {cfg.DATA_DIR}/config.yaml")
    lines.append(f"源码目录: {cfg.PACKAGE_DIR}")
    lines.append(f"项目文档: {cfg.PACKAGE_DIR.parent.parent}/docs/")
    lines.append(f"配置示例: {cfg.PACKAGE_DIR}/config.example.yaml（包含所有配置项及详细说明）")
    lines.append("可用工具见上方当前会话工具列表；可用 config 工具读取/修改配置")
    return "\n".join(lines)

def execute_with_registry(registry, args: ToolArgs) -> str:
    cfg = _config_module()
    action = args.get("action", "read")
    path = args.get("path", "")
    value = args.get("value")

    if action == "list":
        return _build_self_overview(registry) + "\n\n" + _CONFIG_STRUCTURE
    
    if action == "reload":
        try:
            cfg.init_config()
            active_model = cfg._raw.get("active_model", "?")
            model_name = cfg.MODEL_CONFIG.get("model", "?")
            return f"✓ 配置已重新加载\n当前模型: {active_model} ({model_name})"
        except Exception as e:
            return f"Error: 配置重载失败 - {e}"

    if action == "read":
        if not path:
            keys = list(cfg._raw.keys())
            return f"配置顶层键: {', '.join(keys)}"
        parts = path.split(".")
        val, found = _resolve(cfg._raw, parts)
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
        _set(cfg._raw, parts, value)
        import yaml
        with open(cfg._config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg._raw, f, default_flow_style=False, allow_unicode=True)
        logger.info(f"[配置] 已修改 {path} = {value}")
        needs_restart = any(parts[0] == k for k in ("models", "mcp", "plan", "compactor", "thinking", "timeouts", "tool", "runner", "web", "display", "skill_paths", "streaming"))
        hint = "（需重启 mini-ai 生效）" if needs_restart else "（即时生效）"
        if parts[0] == "active_model" and isinstance(value, str):
            hint = "（可用 /model 命令即时切换）"
        return f"已修改 {path} = {json.dumps(value, ensure_ascii=False)} {hint}"

    return f"未知 action: {action}"


def execute(args: ToolArgs) -> str:
    return execute_with_registry(None, args)
