import os
import sys

_COLOR_CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34"}


def is_ci_environment() -> bool:
    """Detects if we're running in a CI environment by checking
    common CI environment variables.
    """
    return os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


def color_text(text: str, color: str) -> str:
    """Returns colored text if output is a terminal
    and NO_COLOR is not set, otherwise returns plain text.
    """
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR") or is_ci_environment():
        return text

    code = _COLOR_CODES.get(color)
    if not code:
        return text
    return f"\033[1;{code}m{text}\033[0m"
