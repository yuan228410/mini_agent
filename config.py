"""配置加载"""
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).parent

with open(PROJECT_DIR / "config.yaml", encoding="utf-8") as f:
    _raw = yaml.safe_load(f)

MODEL_CONFIG = _raw["models"][_raw["active_model"]]
TIMEOUTS = _raw["timeouts"]
COMPACTOR = _raw["compactor"]
TEAMMATE = _raw["teammate"]
TOOL = _raw["tool"]
API_MODE = MODEL_CONFIG.get("api_mode", "openai")
STREAMING = _raw.get("streaming", False)
RUNNER = _raw.get("runner", {"context_usage_limit": 0.88})
