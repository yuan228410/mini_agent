"""配置加载 — 支持延迟初始化和优雅降级。

模块导入时自动尝试加载配置，失败不退出进程。
提供 init_config() 供显式重新加载。
支持配置热加载（通过 ConfigWatcher）。
"""
import copy
import os
import sys
import time
import threading
from pathlib import Path
from typing import Callable, TypedDict, Literal, Any

import yaml

# 延迟导入 logger（避免循环依赖）
logger = None
def _get_logger():
    global logger
    if logger is None:
        from .logger import logger as _logger
        logger = _logger
    return logger

PACKAGE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("MINI_AI_DATA", Path.home() / ".mini_ai"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def user_data_dir(username: str) -> Path:
    """返回用户数据根目录。所有用户统一用 DATA_DIR/users/<name>"""
    if not username:
        username = "default"
    d = DATA_DIR / "users" / username
    d.mkdir(parents=True, exist_ok=True)
    return d


class ConfigError(Exception):
    """配置加载/校验失败"""
    pass


_config_path = DATA_DIR / "config.yaml"

# ── 模块级状态（init_config 填充）──

_config_error: str | None = None
_raw: dict = {}
MODEL_CONFIG: dict = {}
AVAILABLE_MODELS: list[str] = []
TIMEOUTS: dict = {}
COMPACTOR: dict = {}
TEAMMATE: dict = {}
TOOL: dict = {}
IMAGE: dict = {
    "max_size": 10 * 1024 * 1024,           # 10MB
    "compress_threshold": 500 * 1024,       # 500KB
    "compress_max_dimension": 800,          # 800px
    "compress_quality": 85,                 # JPEG quality
}
API_MODE: str = "openai"
STREAMING: bool = True
RUNNER: dict = {"context_usage_limit": 0.88, "max_turns": 20}
THINKING: dict = {"enabled": False, "budget_tokens": 10000, "type": "enabled"}
DISPLAY: dict = {"thinking_mode": "collapsed", "tool_detail": "summary"}
WEB: dict = {"history_limit": 200}
LOGGING: dict = {}
PLAN: dict = {"approval": True}
MCP: dict = {"enabled": False}
SKILL_PATHS: list[Path] = []
SUBAGENT_MODELS: dict = {}
DATABASE: dict = {
    "history": {
        "async_write": None,  # None 表示自动选择（Web 端 true，CLI 端 false）
        "batch_size": 50,
        "batch_timeout": 0.1,
        "queue_size": 10000,
        "retry_count": 3,
    },
    "memory": {
        "cache_size": 10000,
    }
}

# ── 配置热加载 ──

class ConfigWatcher:
    """配置文件变更监听器（基于轮询）
    
    使用方式：
        watcher = ConfigWatcher(config_path, on_change_callback)
        watcher.start()
        # ... 程序运行 ...
        watcher.stop()
    """
    
    def __init__(self, config_path: Path, callback: Callable[[], None], interval: float = 1.0):
        """
        Args:
            config_path: 配置文件路径
            callback: 配置变更时的回调函数
            interval: 轮询间隔（秒）
        """
        self.config_path = config_path
        self.callback = callback
        self.interval = interval
        self._last_mtime: float = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
    
    def start(self) -> None:
        """启动监听线程"""
        if self._thread and self._thread.is_alive():
            return
        
        try:
            self._last_mtime = self.config_path.stat().st_mtime
        except FileNotFoundError:
            self._last_mtime = 0
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True, name="ConfigWatcher")
        self._thread.start()
    
    def stop(self) -> None:
        """停止监听"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
    
    def _watch_loop(self) -> None:
        """监听循环"""
        while not self._stop_event.wait(self.interval):
            try:
                current_mtime = self.config_path.stat().st_mtime
                if current_mtime != self._last_mtime:
                    self._last_mtime = current_mtime
                    # 在主线程中执行回调可能有风险，这里用 try 保护
                    try:
                        self.callback()
                    except Exception as e:
                        print(f"[ConfigWatcher] 回调执行失败: {e}", file=sys.stderr)
            except FileNotFoundError:
                # 配置文件被删除，忽略
                pass
            except OSError:
                # 其他 IO 错误，忽略
                pass


# 模块级监听器实例
_config_watcher: ConfigWatcher | None = None


def start_config_watcher() -> None:
    """启动配置热加载监听（main.py 启动时调用）"""
    global _config_watcher
    if _config_watcher:
        return

    _config_watcher = ConfigWatcher(_config_path, init_config)
    _config_watcher.start()
    _get_logger().info("[Config] 热加载监听已启动")


def stop_config_watcher() -> None:
    """停止配置热加载监听"""
    global _config_watcher
    if _config_watcher:
        _config_watcher.stop()
        _config_watcher = None

# ── 内部：加载 & 校验 ──

def _load_and_validate(config_path: Path) -> dict:
    if not config_path.exists():
        _example = PACKAGE_DIR / "config.example.yaml"
        if _example.exists():
            import shutil
            shutil.copy2(_example, config_path)

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    active_model = raw.get("active_model")
    models = raw.get("models")
    if not active_model:
        raise ConfigError("config.yaml 缺少 active_model 字段")
    if not models or not isinstance(models, dict):
        raise ConfigError("config.yaml 缺少 models 字段")
    if active_model not in models:
        available = ", ".join(models.keys())
        raise ConfigError(f"active_model '{active_model}' 不在 models 中，可选: {available}")

    model_config = copy.deepcopy(models[active_model])
    required_keys = ("api_url", "api_key", "model")
    missing = [k for k in required_keys if k not in model_config]
    if missing:
        raise ConfigError(f"models.{active_model} 缺少必填字段: {', '.join(missing)}")

    return raw


def _apply_config(raw: dict) -> None:
    """从 raw dict 刷新所有模块级配置变量。"""
    global MODEL_CONFIG, AVAILABLE_MODELS, TIMEOUTS, COMPACTOR, TEAMMATE, TOOL, IMAGE
    global API_MODE, STREAMING, RUNNER, THINKING, DISPLAY, WEB, LOGGING, PLAN, MCP, SKILL_PATHS, SUBAGENT_MODELS

    active_model = raw["active_model"]
    models = raw["models"]
    MODEL_CONFIG = copy.deepcopy(models[active_model])
    AVAILABLE_MODELS = list(models.keys())

    # 超时配置（带默认值）
    _timeout_defaults = {
        "llm": 120,
        "llm_connect": 30,
        "llm_retries": 3,
        "llm_retry_delay": 2,
        "teammate_recv": 5,
        "lead_wait": 1800,
        "lead_poll_interval": 2,
        "web_fetch": 20,
    }
    TIMEOUTS = {**_timeout_defaults, **(raw.get("timeouts") or {})}
    
    # 压缩配置
    _compactor_defaults = {
        "context_usage_threshold": 0.8,
        "keep_recent": 50,
        "keep_budget_ratio": 0.2,
        "early_compact_ratio": 0.85,
        "max_cached_summaries": 200,
        "max_summary_sections": 50,
        "context_limit": 50,
    }
    COMPACTOR = {**_compactor_defaults, **(raw.get("compactor") or {})}
    
    # 队友配置
    _teammate_defaults = {
        "max_teammates": 10,
        "max_turns": 20,
        "idle_timeout": 300,
        "max_history": 20,
        "task_timeout": 600,
        "base_tools": [
            "run_command",
            "web_fetch",
            "load_skill",
            "read_file",
            "write_file",
            "edit_file",
            "search_files",
            "list_dir",
        ],
    }
    TEAMMATE = {**_teammate_defaults, **(raw.get("teammate") or {})}
    
    # 工具配置
    _tool_defaults = {
        "max_result_chars": 8000,
    }
    TOOL = {**_tool_defaults, **(raw.get("tool") or {})}
    
    # 图片处理配置
    _image_defaults = {
        "max_size": 10 * 1024 * 1024,
        "compress_threshold": 500 * 1024,
        "compress_max_dimension": 800,
        "compress_quality": 85,
    }
    IMAGE = {**_image_defaults, **(raw.get("image") or {})}
    
    API_MODE = MODEL_CONFIG.get("api_mode", "openai")
    STREAMING = raw.get("streaming", True)
    RUNNER = raw.get("runner") or {"context_usage_limit": 0.88, "max_turns": 20}
    _global_thinking = raw.get("thinking") or {"enabled": False, "budget_tokens": 10000, "type": "enabled"}
    _model_thinking = MODEL_CONFIG.get("thinking") or {}
    THINKING = {**_global_thinking, **_model_thinking}
    DISPLAY = raw.get("display") or {"thinking_mode": "collapsed", "tool_detail": "summary"}
    WEB = raw.get("web") or {"history_limit": 200}
    LOGGING = raw.get("logging") or {}
    PLAN = raw.get("plan") or {"approval": True}
    MCP = raw.get("mcp") or {"enabled": False}
    SKILL_PATHS = [Path(p) for p in (raw.get("skill_paths") or [])]
    SUBAGENT_MODELS = raw.get("subagent_models") or {}
    
    # 数据库配置
    _database_defaults = {
        "history": {
            "async_write": None,
            "batch_size": 50,
            "batch_timeout": 0.1,
            "queue_size": 10000,
            "retry_count": 3,
        },
        "memory": {
            "cache_size": 10000,
        }
    }
    DATABASE = {**_database_defaults, **(raw.get("database") or {})}


# ── 公开 API ──

def init_config() -> None:
    """显式加载/重载配置。失败时设置 _config_error，不退出进程。"""
    global _config_error, _raw

    try:
        _raw = _load_and_validate(_config_path)
        _apply_config(_raw)
        _config_error = None
    except ConfigError as e:
        _config_error = str(e)
        print(f"警告: {e}，使用默认配置", file=sys.stderr)


# ── 模块导入时自动尝试加载（优雅降级）──

init_config()


def get_model_config(name: str) -> dict | None:
    """获取指定名称的模型配置"""
    models = _raw.get("models", {})
    if name not in models:
        return None
    return copy.deepcopy(models[name])


import requests as _requests


class RequestContext:
    __slots__ = ("model_config", "display", "http_session")

    def __init__(self, model_config: dict, display=None, http_session: _requests.Session | None = None):
        self.model_config = model_config
        self.display = display
        self.http_session = http_session or _requests.Session()


def switch_model(name: str) -> str | None:
    """切换模型并持久化到 config.yaml"""
    global API_MODE, THINKING
    models = _raw.get("models", {})
    if name not in models:
        return None
    model_cfg = copy.deepcopy(models[name])
    required = ("api_url", "api_key", "model")
    missing = [k for k in required if k not in model_cfg]
    if missing:
        return f"模型 '{name}' 缺少字段: {', '.join(missing)}"
    _raw["active_model"] = name
    with open(_config_path, "w", encoding="utf-8") as f:
        yaml.dump(_raw, f, default_flow_style=False, allow_unicode=True)
    MODEL_CONFIG.clear()
    MODEL_CONFIG.update(model_cfg)
    API_MODE = MODEL_CONFIG.get("api_mode", "openai")
    _global_thinking = (_raw.get("thinking") or {"enabled": False, "budget_tokens": 10000, "type": "enabled"})
    _model_thinking = model_cfg.get("thinking") or {}
    THINKING = {**_global_thinking, **_model_thinking}
