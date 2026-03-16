"""Tests for the docker module."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyforge_deploy.builders.docker import DockerBuilder


@pytest.fixture
def mock_docker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    # Create the templates directory in the mock root
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "Dockerfile.j2").write_text(
        'FROM python:{{ python_version }}\nCMD ["{{ entry_point }}"]', encoding="utf-8"
    )

    builders_dir = tmp_path / "builders"
    builders_dir.mkdir()

    import pyforge_deploy.builders.docker as docker_mod

    monkeypatch.setattr(docker_mod, "__file__", str(builders_dir / "docker.py"))

    monkeypatch.setattr(docker_mod, "get_python_version", lambda: "3.12")

    def fake_detect_dependencies(x: str) -> dict[str, object]:
        return {}

    monkeypatch.setattr(docker_mod, "detect_dependencies", fake_detect_dependencies)

    return tmp_path


def test_docker_builder_init() -> None:
    builder = DockerBuilder(entry_point="main", image_tag="my-app")
    assert builder.entry_point == "main"
    assert builder.image_tag == "my-app"

    with pytest.raises(ValueError, match="must be alphanumeric"):
        DockerBuilder(image_tag="invalid_tag!")


def test_render_template_success(mock_docker_env: Path) -> None:
    builder = DockerBuilder(entry_point="app", image_tag="test")
    builder.render_template()

    dockerfile = mock_docker_env / "Dockerfile"
    assert dockerfile.exists()
    content = dockerfile.read_text(encoding="utf-8")
    assert "FROM python:3.12" in content
    assert 'CMD ["app"]' in content


def test_render_template_missing_dir(mock_docker_env: Path) -> None:
    shutil.rmtree(mock_docker_env / "templates")
    builder = DockerBuilder()
    with pytest.raises(FileNotFoundError, match="Templates directory not found"):
        builder.render_template()


def test_build_image_success(
    mock_docker_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = DockerBuilder(image_tag="test-image")
    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)

    builder.build_image()
    mock_run.assert_called_once()


def test_build_image_failure(
    mock_docker_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = DockerBuilder(image_tag="test-image")

    def mock_run_fail(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "docker")

    monkeypatch.setattr(subprocess, "run", mock_run_fail)

    with pytest.raises(RuntimeError, match="Docker build process failed"):
        builder.build_image()


def test_deploy_wrapper(mock_docker_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builder = DockerBuilder()
    mock_render = MagicMock()
    mock_build = MagicMock()

    monkeypatch.setattr(builder, "render_template", mock_render)
    monkeypatch.setattr(builder, "build_image", mock_build)

    builder.deploy()
    mock_render.assert_called_once()
    mock_build.assert_called_once()


def test_docker_builder_invalid_entry_point() -> None:
    with pytest.raises(ValueError, match="must be alphanumeric"):
        DockerBuilder(entry_point="invalid!entry")


def test_render_template_jinja_error(
    mock_docker_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = DockerBuilder(entry_point="app", image_tag="test")
    monkeypatch.setattr(
        "pyforge_deploy.builders.docker.get_python_version", lambda: "3.12"
    )
    # Patch Jinja2 to raise error
    import jinja2

    monkeypatch.setattr(
        jinja2.Environment,
        "get_template",
        lambda self, name: (_ for _ in ()).throw(jinja2.TemplateError("fail")),
    )
    with pytest.raises(RuntimeError, match="Failed to render Dockerfile template"):
        builder.render_template()


def test_render_template_write_error(
    mock_docker_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = DockerBuilder(entry_point="app", image_tag="test")
    monkeypatch.setattr(
        "pyforge_deploy.builders.docker.get_python_version", lambda: "3.12"
    )
    import builtins

    monkeypatch.setattr(
        builtins, "open", lambda *a, **kw: (_ for _ in ()).throw(OSError("fail"))
    )
    # Update the match string to reflect the actual error
    with pytest.raises(RuntimeError, match="Failed to render Dockerfile template"):
        builder.render_template()


def test_build_image_file_not_found(
    mock_docker_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = DockerBuilder(image_tag="test-image")

    def mock_run_not_found(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", mock_run_not_found)
    with pytest.raises(RuntimeError, match="Docker executable not found"):
        builder.build_image()


def test_build_image_prints_success(
    mock_docker_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    builder = DockerBuilder(image_tag="test-image")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    builder.build_image()
    out = capsys.readouterr().out
    assert "built successfully" in out
    assert "Building Docker image" in out


def test_build_image_prints_failure(
    mock_docker_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    builder = DockerBuilder(image_tag="test-image")

    def mock_run_fail(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "docker")

    monkeypatch.setattr(subprocess, "run", mock_run_fail)
    with pytest.raises(RuntimeError):
        builder.build_image()
    out = capsys.readouterr().out
    assert "Docker build failed" in out
    assert (
        "Docker build process failed" not in out
    )  # error message is raised, not printed


def test_build_image_prints_not_found(
    mock_docker_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    builder = DockerBuilder(image_tag="test-image")

    def mock_run_not_found(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", mock_run_not_found)
    with pytest.raises(RuntimeError):
        builder.build_image()
    out = capsys.readouterr().out
    assert "Docker executable not found" in out
