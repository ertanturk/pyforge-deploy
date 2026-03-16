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


def status_bar(
    current: int,
    total: int,
    message: str,
    *,
    width: int = 24,
    level: str = "info",
    color: str = "cyan",
) -> None:
    """Emit a status/progress bar message.

    Args:
        current: Current step index.
        total: Total number of steps.
        message: Human-readable status message.
        width: Visual bar width when not in JSON mode.
        level: Log level label.
        color: Color name for non-JSON output.
    """
    safe_total = max(total, 1)
    safe_current = max(0, min(current, safe_total))
    percent = int((safe_current / safe_total) * 100)

    if _json_enabled():
        payload: dict[str, Any] = {
            "level": level,
            "message": message,
            "progress": {
                "current": safe_current,
                "total": safe_total,
                "percent": percent,
            },
        }
        print(json.dumps(payload))
        return

    filled = int((safe_current / safe_total) * width)
    bar = ("#" * filled) + ("-" * (width - filled))
    text = (
        f"[{level.upper()}] [{bar}] {percent:>3}% "
        f"({safe_current}/{safe_total}) {message}"
    )
    print(color_text(text, color))
