"""配置加载"""
import copy
import os
import sys
from pathlib import Path

import yaml

PACKAGE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("MINI_AI_DATA", Path.home() / ".mini_ai"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def user_data_dir(username: str) -> Path:
    """返回用户数据根目录。default 用户用 DATA_DIR 向后兼容，其他用户用 DATA_DIR/users/<name>"""
    if not username or username == "default":
        return DATA_DIR
    d = DATA_DIR / "users" / username
    d.mkdir(parents=True, exist_ok=True)
    return d


class ConfigError(Exception):
    pass


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


_config_path = DATA_DIR / "config.yaml"

try:
    _raw = _load_and_validate(_config_path)
except ConfigError as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)

_active_model = _raw["active_model"]
_models = _raw["models"]
MODEL_CONFIG = copy.deepcopy(_models[_active_model])

TIMEOUTS = (_raw.get("timeouts") or {})
COMPACTOR = (_raw.get("compactor") or {})
TEAMMATE = (_raw.get("teammate") or {})
TOOL = (_raw.get("tool") or {})
API_MODE = MODEL_CONFIG.get("api_mode", "openai")
STREAMING = _raw.get("streaming", False)
RUNNER = (_raw.get("runner") or {"context_usage_limit": 0.88})
THINKING = (_raw.get("thinking") or {"enabled": False, "budget_tokens": 10000})
DISPLAY = (_raw.get("display") or {"thinking_mode": "collapsed", "tool_detail": "summary"})
WEB = (_raw.get("web") or {"history_limit": 200})
PLAN = (_raw.get("plan") or {"approval": True})
SKILL_PATHS = [Path(p) for p in (_raw.get("skill_paths") or [])]


AVAILABLE_MODELS = list(_models.keys())


def get_model_config(name: str) -> dict | None:
    if name not in _models:
        return None
    return copy.deepcopy(_models[name])


import requests as _requests


class RequestContext:
    __slots__ = ("model_config", "display", "http_session")

    def __init__(self, model_config: dict, display=None, http_session: _requests.Session | None = None):
        self.model_config = model_config
        self.display = display
        self.http_session = http_session or _requests.Session()


def switch_model(name: str) -> str | None:
    global API_MODE
    if name not in _models:
        return None
    model_cfg = _models[name]
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
    return None
