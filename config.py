"""配置加载"""
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).parent

with open(PROJECT_DIR / "config.yaml", encoding="utf-8") as f:
    _raw = yaml.safe_load(f)

MODEL_CONFIG = _raw["model"]