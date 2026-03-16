"""Tests for the docker_engine module."""

import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

from pyforge_deploy.builders.docker_engine import (
    detect_dependencies,
    get_clean_final_list,
    get_imports,
    get_local_modules,
    get_python_version,
    get_venv_bin_tools,
)


@pytest.fixture
def mock_project_dir(tmp_path: Path) -> Generator[Path, None, None]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nrequires-python = ">=3.11"\n', encoding="utf-8")

    src_dir = tmp_path / "src" / "my_module"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "core.py").write_text("import json\nimport requests\n", encoding="utf-8")

    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "pytest").touch()
    (venv_bin / "python").touch()

    yield tmp_path


def test_get_python_version_success(
    mock_project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "getcwd", lambda: str(mock_project_dir))
    version = get_python_version()
    assert version == "3.11"


def test_get_python_version_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    expected_v = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert get_python_version() == expected_v


def test_get_venv_bin_tools(mock_project_dir: Path) -> None:
    tools = get_venv_bin_tools(str(mock_project_dir))
    assert "pytest" in tools
    assert "python" not in tools


def test_get_venv_bin_tools_no_venv(tmp_path: Path) -> None:
    # Should return empty set if no venv exists
    assert get_venv_bin_tools(str(tmp_path)) == set()


def _raise_oserror(path: str) -> list[str]:
    raise OSError("fail")


def test_get_venv_bin_tools_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv_path = tmp_path / ".venv"
    bin_path = venv_path / "bin"
    bin_path.mkdir(parents=True)
    monkeypatch.setattr(os, "listdir", _raise_oserror)
    result = get_venv_bin_tools(str(tmp_path))
    assert result == set()


def test_get_local_modules(mock_project_dir: Path) -> None:
    local_mods = get_local_modules(str(mock_project_dir))
    assert "core" in local_mods


def test_get_local_modules_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    monkeypatch.setattr(os, "listdir", _raise_oserror)
    result = get_local_modules(str(tmp_path))
    assert isinstance(result, set)


def test_get_imports(mock_project_dir: Path) -> None:
    imports = get_imports(str(mock_project_dir))
    assert "requests" in imports
    assert "json" in imports


def test_get_imports_empty_file(tmp_path: Path) -> None:
    file_path = tmp_path / "empty.py"
    file_path.write_text("", encoding="utf-8")
    result = get_imports(str(tmp_path))
    assert isinstance(result, set)


def test_get_imports_syntax_error(tmp_path: Path) -> None:
    file_path = tmp_path / "bad.py"
    file_path.write_text("def bad(:", encoding="utf-8")
    result = get_imports(str(tmp_path))
    assert isinstance(result, set)


def test_get_clean_final_list(mock_project_dir: Path) -> None:
    detected_imports = {"requests", "json", "core"}
    dev_tools = {"pytest", "setup"}  # setup should be filtered out

    clean_list = get_clean_final_list(
        detected_imports, dev_tools, str(mock_project_dir)
    )

    assert "requests" in clean_list
    assert "pytest" in clean_list
    assert "json" not in clean_list  # standard library
    assert "core" not in clean_list  # local module
    assert "setup" not in clean_list  # boilerplate filter


def test_get_clean_final_list_stdlib_and_local(tmp_path: Path) -> None:
    (tmp_path / "core.py").touch()

    detected_imports = {"os", "core", "setup"}
    dev_tools = {"pytest"}
    clean_list = get_clean_final_list(detected_imports, dev_tools, str(tmp_path))
    assert "pytest" in clean_list
    assert "os" not in clean_list
    assert "core" not in clean_list
    assert "setup" not in clean_list


def test_detect_dependencies_with_requirements(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    result = detect_dependencies(str(tmp_path))
    assert "requirements.txt" in result["requirement_files"]
    assert "requirements-dev.txt" in result["requirement_files"]


def test_get_python_version_pyproject_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    version = get_python_version()
    assert version == f"{sys.version_info.major}.{sys.version_info.minor}"


def test_get_python_version_pyproject_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("invalid toml", encoding="utf-8")
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    version = get_python_version()
    out = capsys.readouterr().out
    assert version == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert "Failed to detect python version" in out or out == ""
