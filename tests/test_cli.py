import sys
from unittest import mock

import pytest

# Adjust the import path based on your actual project structure.
from pyforge_deploy import cli


def test_missing_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that the CLI exits with code 2 if no command is provided."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "the following arguments are required: command" in captured.err


def test_docker_build_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the docker-build command successfully routing arguments."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pyforge-deploy",
            "docker-build",
            "--entry-point",
            "src/main.py",
            "--image-tag",
            "my-app:1.0",
        ],
    )

    with mock.patch("pyforge_deploy.cli.DockerBuilder") as mock_builder_cls:
        cli.main()

        # Verify the Builder was initialized with the correct CLI arguments
        mock_builder_cls.assert_called_once_with(
            entry_point="src/main.py", image_tag="my-app:1.0"
        )
        # Verify deploy() was called on the instance
        mock_builder_cls.return_value.deploy.assert_called_once()


def test_docker_build_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that docker-build cleanly exits with code 1 on exceptions."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "docker-build"])

    with mock.patch("pyforge_deploy.cli.DockerBuilder") as mock_builder_cls:
        # Simulate a crash during the deployment phase
        mock_builder_cls.return_value.deploy.side_effect = Exception("Build crashed")

        with pytest.raises(SystemExit) as exc_info:
            cli.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Docker build failed: Build crashed" in captured.out


def test_deploy_pypi_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the deploy-pypi command successfully routing arguments."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pyforge-deploy",
            "deploy-pypi",
            "--test",
            "--bump",
            "minor",
            "--version",
            "2.0.0",
        ],
    )

    with mock.patch("pyforge_deploy.cli.PyPIDistributor") as mock_pypi_cls:
        cli.main()

        # Verify distributor was initialized with correct parsed arguments
        mock_pypi_cls.assert_called_once_with(
            target_version="2.0.0", use_test_pypi=True, bump_type="minor"
        )
        mock_pypi_cls.return_value.deploy.assert_called_once()


def test_deploy_pypi_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that deploy-pypi cleanly exits with code 1 on exceptions."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "deploy-pypi"])

    with mock.patch("pyforge_deploy.cli.PyPIDistributor") as mock_pypi_cls:
        mock_pypi_cls.return_value.deploy.side_effect = Exception("Upload failed")

        with pytest.raises(SystemExit) as exc_info:
            cli.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "PyPI deployment failed: Upload failed" in captured.out


def test_show_deps_populated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test the show-deps command outputting valid dependencies."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "show-deps"])

    with mock.patch("pyforge_deploy.cli.detect_dependencies") as mock_detect:
        # Return a mocked report
        mock_detect.return_value = {
            "has_pyproject": True,
            "requirement_files": ["requirements.txt", "requirements-dev.txt"],
        }

        cli.main()

        mock_detect.assert_called_once()
        captured = capsys.readouterr()
        assert "Dependency Report:" in captured.out
        assert "Has pyproject.toml: True" in captured.out
        assert (
            "Requirement files: requirements.txt, requirements-dev.txt" in captured.out
        )


def test_show_deps_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test the show-deps command when no requirements are found."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "show-deps"])

    with mock.patch("pyforge_deploy.cli.detect_dependencies") as mock_detect:
        # Return an empty report
        mock_detect.return_value = {
            "has_pyproject": False,
            "requirement_files": [],
        }

        cli.main()

        captured = capsys.readouterr()
        assert "Requirement files: None" in captured.out


def test_show_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test the show-version command output."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "show-version"])

    with mock.patch("pyforge_deploy.cli.get_dynamic_version") as mock_get_version:
        mock_get_version.return_value = "3.1.4"

        cli.main()

        mock_get_version.assert_called_once()
        captured = capsys.readouterr()
        assert "Current project version: 3.1.4" in captured.out
