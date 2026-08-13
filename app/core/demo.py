import json
from pathlib import Path
from typing import Any

from app.core.config import Settings


def load_demo_config(settings: Settings) -> dict[str, Any]:
    path = Path(settings.demo_config_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
