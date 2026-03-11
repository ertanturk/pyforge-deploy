import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import pytest

from pyforge_deploy.builders import docker_engine


@pytest.fixture  # type: ignore
def mock_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[Path, None, None]:
    """
    Sets up a temporary working directory.
    Ensures that test operations do not pollute the actual project directory.
    """
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def test_get_project_path(mock_env: Path) -> None:
    """Test retrieving the current working directory."""
    assert docker_engine.get_project_path() == str(mock_env)


def test_get_pyproject_path(mock_env: Path) -> None:
    """Test retrieving the pyproject.toml path successfully."""
    expected_path = os.path.join(str(mock_env), "pyproject.toml")
    assert docker_engine.get_pyproject_path() == expected_path


def test_detect_dependencies_all_files(mock_env: Path) -> None:
    """Test dependency detection when standard files are present."""
    (mock_env / "pyproject.toml").touch()
    (mock_env / "requirements.txt").touch()
    (mock_env / "requirements-dev.txt").touch()

    report = docker_engine.detect_dependencies(str(mock_env))

    assert report["has_pyproject"] is True
    assert "requirements.txt" in report["requirement_files"]
    assert "requirements-dev.txt" in report["requirement_files"]


@mock.patch("subprocess.run")
def test_detect_dependencies_pip_freeze(
    mock_run: mock.MagicMock, mock_env: Path
) -> None:
    """Test dependency detection fallback using pip freeze when files are missing."""
    mock_result = mock.MagicMock()
    mock_result.stdout = "requests==2.31.0\npydantic==2.0.0"
    mock_run.return_value = mock_result

    report = docker_engine.detect_dependencies(str(mock_env))

    assert report["has_pyproject"] is False
    assert "requirements-frozen.txt" in report["requirement_files"]

    frozen_path = mock_env / "requirements-frozen.txt"
    assert frozen_path.exists()
    assert "requests==2.31.0" in frozen_path.read_text(encoding="utf-8")


@mock.patch("subprocess.run")
def test_detect_dependencies_pip_freeze_empty(
    mock_run: mock.MagicMock, mock_env: Path
) -> None:
    """Test dependency detection when pip freeze returns no packages."""
    mock_result = mock.MagicMock()
    mock_result.stdout = ""
    mock_run.return_value = mock_result

    report = docker_engine.detect_dependencies(str(mock_env))

    assert report["has_pyproject"] is False
    assert report["requirement_files"] == []
    assert not (mock_env / "requirements-frozen.txt").exists()


@mock.patch("subprocess.run")
def test_detect_dependencies_pip_freeze_error(
    mock_run: mock.MagicMock, mock_env: Path
) -> None:
    """Test graceful failure when pip freeze throws an error."""
    mock_run.side_effect = subprocess.CalledProcessError(
        1, "pip", stderr="Command failed"
    )

    with pytest.raises(RuntimeError, match="Failed to run pip freeze: Command failed"):
        docker_engine.detect_dependencies(str(mock_env))


def test_parse_pyproject_success(mock_env: Path) -> None:
    """Test successfully parsing a valid pyproject.toml."""
    (mock_env / "pyproject.toml").write_text(
        '[project]\nname = "test_app"\n', encoding="utf-8"
    )
    result = docker_engine.parse_pyproject()

    assert result is not None
    assert result["project"]["name"] == "test_app"


def test_parse_pyproject_not_found(mock_env: Path) -> None:
    """Test parsing behavior when pyproject.toml does not exist."""
    result = docker_engine.parse_pyproject()
    assert result is None


@mock.patch("src.pyforge_deploy.builders.docker_engine.toml.load")
def test_parse_pyproject_invalid(
    mock_toml_load: mock.MagicMock, mock_env: Path
) -> None:
    """Test parsing behavior when pyproject.toml has invalid TOML syntax."""
    (mock_env / "pyproject.toml").write_text("invalid syntax", encoding="utf-8")
    mock_toml_load.side_effect = Exception("Parse error")

    result = docker_engine.parse_pyproject()
    assert result is None


def test_get_python_version_valid(mock_env: Path) -> None:
    """Test extracting the minimum Python version from pyproject.toml."""
    (mock_env / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\n', encoding="utf-8"
    )
    assert docker_engine.get_python_version() == "3.11"


def test_get_python_version_no_requires(mock_env: Path) -> None:
    """Test fallback version when requires-python is missing."""
    (mock_env / "pyproject.toml").write_text(
        '[project]\nname = "test"\n', encoding="utf-8"
    )
    expected_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert docker_engine.get_python_version() == expected_version
