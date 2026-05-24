"""配置加载"""
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).parent

with open(PROJECT_DIR / "config.yaml", encoding="utf-8") as f:
    _raw = yaml.safe_load(f)

MODEL_CONFIG = _raw["model"]
TIMEOUTS = _raw["timeouts"]
COMPACTOR = _raw["compactor"]
TEAMMATE = _raw["teammate"]
TOOL = _raw["tool"]
STREAMING = _raw.get("streaming", False)
RUNNER = _raw.get("runner", {"context_usage_limit": 0.88})
