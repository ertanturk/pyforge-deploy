"""Tests for the CLI module."""

import sys
from unittest.mock import MagicMock

import pytest

from pyforge_deploy.cli import main


def test_cli_docker_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["pyforge-deploy", "docker-build", "--image-tag", "test-tag"]
    )

    mock_builder_cls = MagicMock()
    monkeypatch.setattr("pyforge_deploy.cli.DockerBuilder", mock_builder_cls)

    main()
    mock_builder_cls.assert_called_once_with(entry_point=None, image_tag="test-tag")
    mock_builder_cls.return_value.deploy.assert_called_once()


def test_cli_docker_build_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "docker-build"])

    mock_builder = MagicMock()
    mock_builder.return_value.deploy.side_effect = RuntimeError("Mock error")
    monkeypatch.setattr("pyforge_deploy.cli.DockerBuilder", mock_builder)

    with pytest.raises(SystemExit):
        main()

    monkeypatch.setenv("PYFORGE_DEBUG", "1")
    with pytest.raises(RuntimeError, match="Mock error"):
        main()


def test_cli_deploy_pypi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["pyforge-deploy", "deploy-pypi", "--bump", "minor", "--test"]
    )

    mock_dist_cls = MagicMock()
    monkeypatch.setattr("pyforge_deploy.cli.PyPIDistributor", mock_dist_cls)

    main()
    mock_dist_cls.assert_called_once_with(
        target_version=None, use_test_pypi=True, bump_type="minor"
    )
    mock_dist_cls.return_value.deploy.assert_called_once()


def test_cli_deploy_pypi_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "deploy-pypi"])

    mock_dist = MagicMock()
    mock_dist.return_value.deploy.side_effect = ValueError("Missing token")
    monkeypatch.setattr("pyforge_deploy.cli.PyPIDistributor", mock_dist)

    with pytest.raises(SystemExit):
        main()


def test_cli_show_deps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "show-deps"])

    def fake_detect_dependencies(x: str) -> dict[str, list[str]]:
        return {"has_pyproject": True, "requirement_files": ["requirements.txt"]}  # type: ignore

    monkeypatch.setattr(
        "pyforge_deploy.cli.detect_dependencies",
        fake_detect_dependencies,
    )

    main()
    captured = capsys.readouterr()
    assert "Dependency Report:" in captured.out
    assert "requirements.txt" in captured.out


def test_cli_show_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "show-version"])
    monkeypatch.setattr("pyforge_deploy.cli.get_dynamic_version", lambda: "9.9.9")

    main()
    captured = capsys.readouterr()
    assert "Current project version: 9.9.9" in captured.out


def test_cli_invalid_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "not-a-command"])
    with pytest.raises(SystemExit):
        main()


def test_cli_argparse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy"])  # No command
    with pytest.raises(SystemExit):
        main()
