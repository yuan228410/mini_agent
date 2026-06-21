"""状态配置接口"""
import time
import yaml

from fastapi import APIRouter, Query

from ... import __version__
from ...application import config_service
from ...config import _raw, _config_path, AVAILABLE_MODELS, switch_model as _switch_model
from ...logger import logger
from ..route_types import (
    AddModelRequest,
    AddModelResponse,
    ConfigResponse,
    McpServerAddRequest,
    McpServerAddResponse,
    McpServerRemoveResponse,
    RemoveModelRequest,
    RemoveModelResponse,
    RouteErrorResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
    SystemPromptResponse,
    ToolsResponse,
)
from ..runtime_helpers import config_preview_dependencies, current_settings_snapshot

router = APIRouter()

@router.get("/config")
async def get_config(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default="")) -> ConfigResponse | RouteErrorResponse:
    _t0 = time.time()
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        session_id = "default"
    result = config_service.config_summary(
        current_settings_snapshot(),
        config_preview_dependencies(),
        version=__version__,
        username=username,
        session_id=session_id,
        workspace=workspace,
    )
    logger.debug(f"[perf] get_config sid={session_id} ws={workspace} time={time.time()-_t0:.3f}s")
    return result


@router.get("/config/system-prompt")
async def get_system_prompt(username: str = Query(default=""), workspace: str = Query(default="")) -> SystemPromptResponse | RouteErrorResponse:
    """获取完整系统提示词（含字符数和 token 估算）"""
    if not username:
        return {"error": "缺少 username"}
    
    return config_service.system_prompt_preview(config_preview_dependencies(), username=username, workspace=workspace)


@router.get("/config/tools")
async def get_tools(username: str = Query(default=""), workspace: str = Query(default=""), session_id: str = Query(default="default")) -> ToolsResponse:
    """获取当前会话工具定义（含字符数和 token 估算）。"""
    from ..deps import SUBAGENT_LOADER, _MCP_LOADER

    return config_service.tools_preview(
        config_preview_dependencies(),
        username=username,
        workspace=workspace,
        session_id=session_id,
        subagent_loader=SUBAGENT_LOADER,
        mcp_loader=_MCP_LOADER,
    )


@router.get("/settings")
async def get_settings() -> SettingsResponse:
    from ...config import (
        _raw, STREAMING, THINKING, DISPLAY, COMPACTOR, TIMEOUTS,
        RUNNER, PLAN, TOOL, WEB, LOGGING, AVAILABLE_MODELS, MODEL_CONFIG,
    )
    models_safe = {}
    for name, cfg in _raw.get("models", {}).items():
        models_safe[name] = {
            "api_url": cfg.get("api_url", ""),
            "api_mode": cfg.get("api_mode", "openai"),
            "model": cfg.get("model", ""),
            "context_length": cfg.get("context_length", 256000),
            "temperature": cfg.get("temperature"),
            "max_tokens": cfg.get("max_tokens"),
            "top_p": cfg.get("top_p"),
            "reasoning_effort": cfg.get("reasoning_effort"),
            "thinking": cfg.get("thinking"),
        }
    return {
        "active_model": _raw.get("active_model", ""),
        "models": models_safe,
        "streaming": STREAMING,
        "thinking": THINKING,
        "display": DISPLAY,
        "compactor": COMPACTOR,
        "timeouts": TIMEOUTS,
        "runner": RUNNER,
        "plan": PLAN,
        "tool": TOOL,
        "web": WEB,
        "logging": LOGGING,
    }


@router.put("/settings")
async def update_settings(body: SettingsUpdateRequest) -> SettingsUpdateResponse:

    updated_sections = []

    if "active_model" in body:
        name = body["active_model"]
        if name in _raw.get("models", {}):
            _raw["active_model"] = name
            _switch_model(name)
            updated_sections.append("active_model")

    if "thinking" in body:
        thinking = body["thinking"]
        if isinstance(thinking, dict):
            _raw.setdefault("thinking", {}).update(thinking)
            updated_sections.append("thinking")

    if "display" in body:
        display = body["display"]
        if isinstance(display, dict):
            _raw.setdefault("display", {}).update(display)
            from ...config import DISPLAY
            DISPLAY.update(display)
            updated_sections.append("display")

    if "compactor" in body:
        compactor = body["compactor"]
        if isinstance(compactor, dict):
            _raw.setdefault("compactor", {}).update(compactor)
            from ...config import COMPACTOR
            COMPACTOR.update(compactor)
            updated_sections.append("compactor")

    if "tool" in body:
        tool = body["tool"]
        if isinstance(tool, dict):
            _raw.setdefault("tool", {}).update(tool)
            from ...config import TOOL
            TOOL.update(tool)
            updated_sections.append("tool")

    if "runner" in body:
        runner = body["runner"]
        if isinstance(runner, dict):
            _raw.setdefault("runner", {}).update(runner)
            from ...config import RUNNER
            RUNNER.update(runner)
            updated_sections.append("runner")

    if "plan" in body:
        plan = body["plan"]
        if isinstance(plan, dict):
            _raw.setdefault("plan", {}).update(plan)
            from ...config import PLAN
            PLAN.update(plan)
            updated_sections.append("plan")

    if "logging" in body:
        logging_cfg = body["logging"]
        if isinstance(logging_cfg, dict):
            _raw.setdefault("logging", {}).update(logging_cfg)
            from ...config import LOGGING
            LOGGING.update(logging_cfg)
            updated_sections.append("logging")

    if "timeouts" in body:
        timeouts = body["timeouts"]
        if isinstance(timeouts, dict):
            _raw.setdefault("timeouts", {}).update(timeouts)
            from ...config import TIMEOUTS
            TIMEOUTS.update(timeouts)
            updated_sections.append("timeouts")

    if "web" in body:
        web_cfg = body["web"]
        if isinstance(web_cfg, dict):
            _raw.setdefault("web", {}).update(web_cfg)
            from ...config import WEB
            WEB.update(web_cfg)
            updated_sections.append("web")

    if "streaming" in body:
        _raw["streaming"] = bool(body["streaming"])
        from ...config import STREAMING
        import mini_ai.config as _cfg
        _cfg.STREAMING = bool(body["streaming"])
        updated_sections.append("streaming")

    if "model_config" in body:
        model_updates = body["model_config"]
        model_name = model_updates.get("name", "")
        if model_name and model_name in _raw.get("models", {}):
            model_cfg = _raw["models"][model_name]
            for key in ("temperature", "max_tokens", "top_p", "reasoning_effort", "context_length"):
                if key in model_updates:
                    val = model_updates[key]
                    if val is None:
                        model_cfg.pop(key, None)
                    else:
                        model_cfg[key] = val
            if "thinking" in model_updates:
                thinking = model_updates["thinking"]
                if thinking is None:
                    model_cfg.pop("thinking", None)
                else:
                    model_cfg["thinking"] = thinking
            updated_sections.append(f"model_config.{model_name}")

    if updated_sections:
        with open(_config_path, "w", encoding="utf-8") as f:
            yaml.dump(_raw, f, default_flow_style=False, allow_unicode=True)

    return {"status": "ok", "updated": updated_sections}


@router.post("/settings/add_model")
async def add_model(body: AddModelRequest) -> AddModelResponse | RouteErrorResponse:

    name = body.get("name", "").strip()
    if not name:
        return {"error": "模型名称不能为空"}
    if name in _raw.get("models", {}):
        return {"error": f"模型 '{name}' 已存在"}

    api_mode = body.get("api_mode", "openai")
    model_cfg = {
        "api_key": body.get("api_key", ""),
        "api_url": body.get("api_url", ""),
        "api_mode": api_mode,
        "model": body.get("model", ""),
        "context_length": body.get("context_length", 256000),
    }
    if body.get("temperature") is not None:
        model_cfg["temperature"] = body["temperature"]
    if body.get("headers"):
        model_cfg["headers"] = body["headers"]

    _raw.setdefault("models", {})[name] = model_cfg
    AVAILABLE_MODELS.append(name)

    with open(_config_path, "w", encoding="utf-8") as f:
        yaml.dump(_raw, f, default_flow_style=False, allow_unicode=True)

    return {"status": "ok", "name": name}


@router.delete("/settings/remove_model")
async def remove_model(body: RemoveModelRequest) -> RemoveModelResponse | RouteErrorResponse:

    name = body.get("name", "").strip()
    if not name:
        return {"error": "模型名称不能为空"}
    if name not in _raw.get("models", {}):
        return {"error": f"模型 '{name}' 不存在"}
    if len(_raw.get("models", {})) <= 1:
        return {"error": "至少保留一个模型"}

    was_active = _raw.get("active_model") == name
    del _raw["models"][name]
    if name in AVAILABLE_MODELS:
        AVAILABLE_MODELS.remove(name)

    if was_active:
        first = next(iter(_raw["models"]))
        _raw["active_model"] = first
        _switch_model(first)

    with open(_config_path, "w", encoding="utf-8") as f:
        yaml.dump(_raw, f, default_flow_style=False, allow_unicode=True)

    return {"status": "ok", "removed": name, "new_active": _raw.get("active_model") if was_active else None}


@router.post("/settings/mcp/add")
async def add_mcp_server(body: McpServerAddRequest) -> McpServerAddResponse | RouteErrorResponse:

    name = body.get("name", "").strip()
    server_type = body.get("type", "stdio")

    if not name:
        return {"error": "服务器名称不能为空"}

    mcp_cfg = _raw.setdefault("mcp", {"enabled": True})
    servers = mcp_cfg.setdefault("servers", {})
    if name in servers:
        return {"error": f"MCP 服务器 '{name}' 已存在"}

    server_entry = {"type": server_type}
    if server_type == "stdio":
        command = body.get("command", "").strip()
        if not command:
            return {"error": "stdio 服务器需要 command"}
        server_entry["command"] = command
        if body.get("args"):
            server_entry["args"] = body["args"]
    elif server_type in ("streamable_http", "sse"):
        url = body.get("url", "").strip()
        if not url:
            return {"error": "HTTP 服务器需要 url"}
        server_entry["url"] = url
        if body.get("headers"):
            server_entry["headers"] = body["headers"]

    mcp_cfg["enabled"] = True
    servers[name] = server_entry

    with open(_config_path, "w", encoding="utf-8") as f:
        yaml.dump(_raw, f, default_flow_style=False, allow_unicode=True)

    from ..deps import _init_mcp
    try:
        _init_mcp()
    except Exception:
        pass

    return {"status": "ok", "name": name}


@router.delete("/settings/mcp/{name}")
async def remove_mcp_server(name: str, username: str = "") -> McpServerRemoveResponse | RouteErrorResponse:

    mcp_cfg = _raw.get("mcp", {})
    servers = mcp_cfg.get("servers", {})
    if name not in servers:
        return {"error": f"MCP 服务器 '{name}' 不存在"}

    del servers[name]

    with open(_config_path, "w", encoding="utf-8") as f:
        yaml.dump(_raw, f, default_flow_style=False, allow_unicode=True)

    return {"status": "ok", "removed": name}
