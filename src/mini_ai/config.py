"""配置加载"""
import os
import sys
from pathlib import Path

import yaml

PACKAGE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("MINI_AI_DATA", Path.home() / ".mini_ai"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_config_path = DATA_DIR / "config.yaml"
if not _config_path.exists():
    _example = PACKAGE_DIR / "config.example.yaml"
    if _example.exists():
        import shutil
        shutil.copy2(_example, _config_path)

with open(_config_path, encoding="utf-8") as f:
    _raw = yaml.safe_load(f) or {}

_active_model = _raw.get("active_model")
_models = _raw.get("models")
if not _active_model:
    print(f"Error: config.yaml 缺少 active_model 字段", file=sys.stderr)
    sys.exit(1)
if not _models or not isinstance(_models, dict):
    print(f"Error: config.yaml 缺少 models 字段", file=sys.stderr)
    sys.exit(1)
if _active_model not in _models:
    available = ", ".join(_models.keys())
    print(f"Error: active_model '{_active_model}' 不在 models 中，可选: {available}", file=sys.stderr)
    sys.exit(1)

MODEL_CONFIG = _models[_active_model]
_required_model_keys = ("api_url", "api_key", "model")
_missing = [k for k in _required_model_keys if k not in MODEL_CONFIG]
if _missing:
    print(f"Error: models.{_active_model} 缺少必填字段: {', '.join(_missing)}", file=sys.stderr)
    sys.exit(1)

TIMEOUTS = _raw.get("timeouts", {})
COMPACTOR = _raw.get("compactor", {})
TEAMMATE = _raw.get("teammate", {})
TOOL = _raw.get("tool", {})
API_MODE = MODEL_CONFIG.get("api_mode", "openai")
STREAMING = _raw.get("streaming", False)
RUNNER = _raw.get("runner", {"context_usage_limit": 0.88})
THINKING = _raw.get("thinking", {"enabled": False, "budget_tokens": 10000})
DISPLAY = _raw.get("display", {"thinking_mode": "collapsed", "tool_detail": "summary"})
SKILL_PATHS = [Path(p) for p in _raw.get("skill_paths", [])]
