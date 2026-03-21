"""Tests for plugin-related configuration helpers."""

from __future__ import annotations

import pytest

import pyforge_deploy.config as config_mod


def test_get_plugins_config_returns_nested_plugins_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should parse [tool.pyforge-deploy.plugins] when present."""
    monkeypatch.setattr(
        config_mod,
        "get_tool_config",
        lambda: {"plugins": {"before_build": ["ruff check ."]}},
    )

    plugins = config_mod.get_plugins_config()
    assert plugins == {"before_build": ["ruff check ."]}


def test_get_plugins_config_returns_empty_on_invalid_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-dict plugin table should safely fall back to empty mapping."""
    monkeypatch.setattr(config_mod, "get_tool_config", lambda: {"plugins": "bad"})
    assert config_mod.get_plugins_config() == {}


def test_get_plugin_commands_normalizes_string_and_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Helper should convert both list and string plugin entries to command lists."""
    monkeypatch.setattr(
        config_mod,
        "get_plugins_config",
        lambda: {
            "before_build": ["ruff check .", "  ", 123],
            "after_build": "echo done",
        },
    )

    assert config_mod.get_plugin_commands("before_build") == ["ruff check ."]
    assert config_mod.get_plugin_commands("after_build") == ["echo done"]
    assert config_mod.get_plugin_commands("unknown") == []
