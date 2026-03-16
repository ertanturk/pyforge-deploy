import json
import os
from typing import Any

from .colors import color_text


def _json_enabled() -> bool:
    return os.environ.get("PYFORGE_JSON_LOGS", "false").lower() in {"1", "true", "yes"}


def log(message: str, level: str = "info", color: str = "blue") -> None:
    """Emit a log message. If JSON mode enabled, emit structured JSON.

    Otherwise print colored human-readable output.
    """
    if _json_enabled():
        payload: dict[str, Any] = {"level": level, "message": message}
        print(json.dumps(payload))
    else:
        print(color_text(f"[{level.upper()}] {message}", color))
