import textwrap
from pathlib import Path

from pytest import MonkeyPatch

from pyforge_deploy.config import (
    get_bool_setting,
    get_int_setting,
    get_list_setting,
    resolve_setting,
)


def write_pyproject(tmp_path: Path, content: str) -> None:
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")


def test_resolve_precedence_cli(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    # tool config contains a value, but CLI should take precedence
    content = textwrap.dedent(
        """
        [tool.pyforge-deploy]
        docker_image = "from-tool"
        """
    )
    write_pyproject(tmp_path, content)
    monkeypatch.chdir(tmp_path)

    val = resolve_setting(
        "cli-value", "docker_image", env_keys=("DOCKER_IMAGE",), default=None
    )
    assert val == "cli-value"


def test_resolve_tool_config(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    content = textwrap.dedent(
        """
        [tool.pyforge-deploy]
        example_key = "tool-value"
        """
    )
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    val = resolve_setting(None, "example_key", env_keys=("EXAMPLE_KEY",), default=None)
    assert val == "tool-value"


def test_get_bool_and_int_and_list_from_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MY_BOOL", "true")
    monkeypatch.setenv("MY_INT", "5")
    monkeypatch.setenv("MY_LIST", "a,b,c")

    b = get_bool_setting(None, "unused", env_keys=("MY_BOOL",), default=False)
    i = get_int_setting(None, "unused", env_keys=("MY_INT",), default=0)
    lst = get_list_setting(None, "unused", env_keys=("MY_LIST",), default=None)

    assert b is True
    assert i == 5
    assert lst == ["a", "b", "c"]


def test_resolve_falls_back_to_env_when_tool_config_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    """Broken pyproject loading should not block environment fallbacks."""

    def boom() -> dict[str, object]:
        raise RuntimeError("invalid TOML")

    monkeypatch.setattr("pyforge_deploy.config.get_tool_config", boom)
    monkeypatch.setenv("EXAMPLE_KEY", "env-value")

    val = resolve_setting(None, "example_key", env_keys=("EXAMPLE_KEY",), default=None)
    assert val == "env-value"
