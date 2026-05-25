"""配置加载"""
import os
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
    _raw = yaml.safe_load(f)

MODEL_CONFIG = _raw["models"][_raw["active_model"]]
TIMEOUTS = _raw["timeouts"]
COMPACTOR = _raw["compactor"]
TEAMMATE = _raw["teammate"]
TOOL = _raw["tool"]
API_MODE = MODEL_CONFIG.get("api_mode", "openai")
STREAMING = _raw.get("streaming", False)
RUNNER = _raw.get("runner", {"context_usage_limit": 0.88})
THINKING = _raw.get("thinking", {"enabled": False, "budget_tokens": 10000})
DISPLAY = _raw.get("display", {"thinking_mode": "collapsed", "tool_detail": "summary"})
