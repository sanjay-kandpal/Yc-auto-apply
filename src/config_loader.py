from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid config at {CONFIG_PATH}")
    return data


def repo_path(relative: str) -> Path:
    return ROOT / relative
