import os
import sys

_COLOR_CODES = {
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "gray": "90",
}


def is_ci_environment() -> bool:
    """Detects if we're running in a CI environment by checking
    common CI environment variables.
    """
    return os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


def color_text(text: str, color: str, bold: bool = True) -> str:
    """Returns colored text.
    Respects NO_COLOR, FORCE_COLOR, and enables colors in GitHub Actions.
    """

    if os.environ.get("NO_COLOR"):
        return text

    force_color = (
        os.environ.get("FORCE_COLOR") == "1"
        or os.environ.get("GITHUB_ACTIONS") == "true"
    )

    if not force_color and not sys.stdout.isatty():
        return text

    code = _COLOR_CODES.get(color)
    if not code:
        return text

    style = "1;" if bold else ""
    return f"\033[{style}{code}m{text}\033[0m"
