"""Tests for the colors module."""

import pytest

from pyforge_deploy.colors import color_text, is_ci_environment


def test_color_text_valid_color() -> None:
    """Test that valid colors return correctly formatted ANSI strings."""
    assert color_text("Hello", "red") == "\033[1;31mHello\033[0m"
    assert color_text("World", "green") == "\033[1;32mWorld\033[0m"
    assert color_text("Warning", "yellow") == "\033[1;33mWarning\033[0m"
    assert color_text("Info", "blue") == "\033[1;34mInfo\033[0m"


def test_color_text_invalid_color() -> None:
    """Test that an invalid color returns the plain text."""
    assert color_text("Plain", "purple") == "Plain"
    assert color_text("Text", "unknown") == "Text"


def test_color_text_empty_string() -> None:
    """Test that empty strings are handled correctly."""
    assert color_text("", "red") == "\033[1;31m\033[0m"
    assert color_text("", "invalid") == ""


@pytest.mark.parametrize(
    ("env_key", "env_value"),
    [
        ("CI", "1"),
        ("CI", "true"),
        ("GITHUB_ACTIONS", "1"),
    ],
)
def test_is_ci_environment_accepts_common_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    env_value: str,
) -> None:
    """CI detection should honor common truthy values, not only literal 'true'."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv(env_key, env_value)

    assert is_ci_environment() is True
