_COLOR_CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34"}


def color_text(text: str, color: str) -> str:
    code = _COLOR_CODES.get(color)
    if not code:
        return text
    return f"\033[1;{code}m{text}\033[0m"
