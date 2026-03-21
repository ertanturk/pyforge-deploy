import os


def _truthy(value: str | None) -> bool:
    """Return True for common truthy env values."""
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _log(message: str, color: str = "gray", verbose: bool = False) -> None:
    if verbose or os.environ.get("PYFORGE_DEBUG_COLORS") == "1":
        print(f"[COLORS] \033[90m{message}\033[0m")


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
    ci = _truthy(os.environ.get("CI")) or _truthy(os.environ.get("GITHUB_ACTIONS"))
    _log(
        f"is_ci_environment: {ci}",
        "gray",
        verbose=os.environ.get("PYFORGE_DEBUG_COLORS") == "1",
    )
    return ci


def color_text(text: str, color: str, bold: bool = True) -> str:
    """Returns colored text.
    Respects NO_COLOR, FORCE_COLOR, and enables colors in GitHub Actions.
    """
    if os.environ.get("NO_COLOR"):
        _log(
            "NO_COLOR set, returning plain text",
            "gray",
            verbose=os.environ.get("PYFORGE_DEBUG_COLORS") == "1",
        )
        return text

    # For tests and normal CLI usage we emit ANSI coloring by default
    # unless NO_COLOR is set or an unknown color is requested.
    # Honor FORCE_COLOR and GITHUB_ACTIONS as legacy triggers.
    force_color = (  # noqa: F841
        os.environ.get("FORCE_COLOR") == "1"
        or os.environ.get("GITHUB_ACTIONS") == "true"
    )

    code = _COLOR_CODES.get(color)
    if not code:
        _log(
            f"Unknown color '{color}', returning plain text",
            "gray",
            verbose=os.environ.get("PYFORGE_DEBUG_COLORS") == "1",
        )
        return text

    style = "1;" if bold else ""
    colored = f"\033[{style}{code}m{text}\033[0m"
    _log(
        f"color_text: '{text}' as '{colored}'",
        color,
        verbose=os.environ.get("PYFORGE_DEBUG_COLORS") == "1",
    )
    return colored
