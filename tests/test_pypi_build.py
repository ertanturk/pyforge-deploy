import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import pytest

from pyforge_deploy.builders import docker, docker_engine


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


def test_get_python_version_invalid_match(mock_env: Path) -> None:
    """Test fallback version when requires-python string is malformed."""
    (mock_env / "pyproject.toml").write_text(
        '[project]\nrequires-python = "latest"\n', encoding="utf-8"
    )
    expected_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert docker_engine.get_python_version() == expected_version


def test_docker_builder_init(mock_env: Path) -> None:
    """Test initialization of DockerBuilder properties."""
    builder = docker.DockerBuilder(entry_point="src/main.py")

    assert builder.entry_point == "src/main.py"
    assert builder.image_tag == mock_env.name.lower().replace(" ", "-")
    assert builder.dockerfile_path == mock_env / "Dockerfile"


def test_render_template_success(mock_env: Path) -> None:
    """Test generating a Dockerfile from Jinja2 template successfully."""
    builder = docker.DockerBuilder(entry_point="main.py")

    # Use context managers to mock internal dependencies AFTER object initialization
    with (
        mock.patch("src.pyforge_deploy.builders.docker.Path") as mock_path_cls,
        mock.patch("src.pyforge_deploy.builders.docker.Environment") as mock_env_cls,
        mock.patch("builtins.open", new_callable=mock.mock_open) as mock_open_func,
        mock.patch(
            "src.pyforge_deploy.builders.docker.detect_dependencies",
            return_value={"has_pyproject": True, "requirement_files": []},
        ),
        mock.patch(
            "src.pyforge_deploy.builders.docker.get_python_version", return_value="3.12"
        ),
    ):
        # Mock the templates_dir.exists() to pass the validation check
        mock_templates_dir = mock.MagicMock()
        mock_templates_dir.exists.return_value = True
        mock_path_cls.return_value.parent.parent.__truediv__.return_value = (
            mock_templates_dir
        )

        # Setup Environment and Template mocks
        mock_template = mock.MagicMock()
        mock_template.render.return_value = "DUMMY DOCKERFILE CONTENT"
        mock_env_cls.return_value.get_template.return_value = mock_template

        builder.render_template()

        # Assert rendering parameters
        mock_template.render.assert_called_once_with(
            python_version="3.12",
            report={"has_pyproject": True, "requirement_files": []},
            entry_point="main.py",
        )

        # Assert file writing
        mock_open_func.assert_called_once_with(
            builder.dockerfile_path, "w", encoding="utf-8"
        )
        mock_open_func.return_value.write.assert_called_once_with(
            "DUMMY DOCKERFILE CONTENT"
        )


def test_render_template_not_found(mock_env: Path) -> None:
    """Test rendering raises FileNotFoundError when templates folder is missing."""
    builder = docker.DockerBuilder()

    with (
        mock.patch("src.pyforge_deploy.builders.docker.Path") as mock_path_cls,
        mock.patch("src.pyforge_deploy.builders.docker.detect_dependencies"),
        mock.patch("src.pyforge_deploy.builders.docker.get_python_version"),
    ):
        mock_templates_dir = mock.MagicMock()
        mock_templates_dir.exists.return_value = False
        mock_path_cls.return_value.parent.parent.__truediv__.return_value = (
            mock_templates_dir
        )

        with pytest.raises(FileNotFoundError, match="Templates directory not found"):
            builder.render_template()


def test_render_template_render_error(mock_env: Path) -> None:
    """Test rendering gracefully wraps Jinja2 rendering errors."""
    builder = docker.DockerBuilder()

    with (
        mock.patch("src.pyforge_deploy.builders.docker.Path") as mock_path_cls,
        mock.patch("src.pyforge_deploy.builders.docker.Environment") as mock_env_cls,
        mock.patch("src.pyforge_deploy.builders.docker.detect_dependencies"),
        mock.patch("src.pyforge_deploy.builders.docker.get_python_version"),
    ):
        mock_templates_dir = mock.MagicMock()
        mock_templates_dir.exists.return_value = True
        mock_path_cls.return_value.parent.parent.__truediv__.return_value = (
            mock_templates_dir
        )

        mock_template = mock.MagicMock()
        mock_template.render.side_effect = Exception("Jinja rendering crashed")
        mock_env_cls.return_value.get_template.return_value = mock_template

        with pytest.raises(
            RuntimeError,
            match="Failed to render Dockerfile template: Jinja rendering crashed",
        ):
            builder.render_template()


def test_render_template_write_error(mock_env: Path) -> None:
    """Test rendering gracefully wraps file system I/O errors."""
    builder = docker.DockerBuilder()

    with (
        mock.patch("src.pyforge_deploy.builders.docker.Path") as mock_path_cls,
        mock.patch("src.pyforge_deploy.builders.docker.Environment"),
        mock.patch("builtins.open", side_effect=PermissionError("Access denied")),
        mock.patch("src.pyforge_deploy.builders.docker.detect_dependencies"),
        mock.patch("src.pyforge_deploy.builders.docker.get_python_version"),
    ):
        mock_templates_dir = mock.MagicMock()
        mock_templates_dir.exists.return_value = True
        mock_path_cls.return_value.parent.parent.__truediv__.return_value = (
            mock_templates_dir
        )

        with pytest.raises(
            RuntimeError, match="Failed to write Dockerfile: Access denied"
        ):
            builder.render_template()


@mock.patch("subprocess.run")
def test_build_image_success(mock_run: mock.MagicMock, mock_env: Path) -> None:
    """Test successfully triggering a docker build command."""
    builder = docker.DockerBuilder(image_tag="my-test-app")
    builder.build_image()

    mock_run.assert_called_once_with(
        ["docker", "build", "-t", "my-test-app", "."], check=True, cwd=builder.base_dir
    )


@mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "docker"))
def test_build_image_process_error(mock_run: mock.MagicMock, mock_env: Path) -> None:
    """Test Docker build handling when the subprocess throws an error."""
    builder = docker.DockerBuilder()

    with pytest.raises(RuntimeError, match="Docker build process failed"):
        builder.build_image()


@mock.patch("subprocess.run", side_effect=FileNotFoundError("docker not found"))
def test_build_image_file_not_found(mock_run: mock.MagicMock, mock_env: Path) -> None:
    """Test Docker build handling when Docker engine is not installed/running."""
    builder = docker.DockerBuilder()

    with pytest.raises(RuntimeError, match="Docker executable not found"):
        builder.build_image()


def test_deploy(mock_env: Path) -> None:
    """Test deploy method correctly orchestrates render and build."""
    builder = docker.DockerBuilder()

    # Mock class methods to avoid executing logic during deploy check
    builder.render_template = mock.MagicMock()
    builder.build_image = mock.MagicMock()

    builder.deploy()

    builder.render_template.assert_called_once()
    builder.build_image.assert_called_once()
