"""Where settings live: config/*.json at the repository root, or CRV_CONFIG_DIR."""

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return Path(os.environ.get("CRV_CONFIG_DIR", ROOT / "config"))


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((config_dir() / name).read_text())
    return data
