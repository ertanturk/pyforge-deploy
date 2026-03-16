"""Tests for the CLI module."""

import sys
from unittest.mock import MagicMock

import pytest

import pyforge_deploy.cli as cli_mod
from pyforge_deploy.cli import main


def test_cli_docker_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["pyforge-deploy", "docker-build", "--image-tag", "test-tag"]
    )

    mock_builder_cls = MagicMock()
    monkeypatch.setattr(cli_mod, "DockerBuilder", mock_builder_cls)

    main()
    mock_builder_cls.assert_called_once_with(entry_point=None, image_tag="test-tag")
    mock_builder_cls.return_value.deploy.assert_called_once()


def test_cli_docker_build_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "docker-build"])

    mock_builder = MagicMock()
    mock_builder.return_value.deploy.side_effect = RuntimeError("Mock error")
    monkeypatch.setattr(cli_mod, "DockerBuilder", mock_builder)

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
    monkeypatch.setattr(cli_mod, "PyPIDistributor", mock_dist_cls)

    main()
    mock_dist_cls.assert_called_once_with(
        target_version=None, use_test_pypi=True, bump_type="minor"
    )
    mock_dist_cls.return_value.deploy.assert_called_once()


def test_cli_deploy_pypi_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "deploy-pypi"])

    mock_dist = MagicMock()
    mock_dist.return_value.deploy.side_effect = ValueError("Missing token")
    monkeypatch.setattr(cli_mod, "PyPIDistributor", mock_dist)

    with pytest.raises(SystemExit):
        main()


def test_cli_show_deps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "show-deps"])

    def fake_detect_dependencies(x: str) -> dict[str, object]:
        return {"has_pyproject": True, "requirement_files": ["requirements.txt"]}

    monkeypatch.setattr(cli_mod, "detect_dependencies", fake_detect_dependencies)

    main()
    captured = capsys.readouterr()
    assert "Dependency Report:" in captured.out
    assert "requirements.txt" in captured.out


def test_cli_show_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "show-version"])
    monkeypatch.setattr(cli_mod, "get_dynamic_version", lambda: "9.9.9")

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


def test_cli_status_shows_release_and_docker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Status command should include release and docker image checks."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "status"])
    monkeypatch.setattr(cli_mod, "get_project_details", lambda: ("demo-app", "1.0.0"))
    monkeypatch.setattr(cli_mod, "get_dynamic_version", lambda: "1.2.3")
    monkeypatch.setattr(cli_mod, "fetch_latest_version", lambda _: "1.2.2")
    monkeypatch.setattr(cli_mod, "_get_last_release_tag", lambda: "v1.2.2")
    monkeypatch.setattr(
        cli_mod,
        "_get_last_release_published_at",
        lambda _: "2026-03-16 10:00:00 UTC",
    )
    monkeypatch.setattr(cli_mod, "_check_docker_image_status", lambda _: "Exists")

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_token":
            return None
        if tool_key == "docker_user":
            return "demo"
        if tool_key == "docker_image":
            return "demo/demo-app:1.2.3"
        return default

    monkeypatch.setattr(cli_mod, "resolve_setting", fake_resolve_setting)

    main()
    captured = capsys.readouterr().out
    assert "Last Release" in captured
    assert "v1.2.2" in captured
    assert "Release Published" in captured
    assert "2026-03-16 10:00:00 UTC" in captured
    assert "Image Check" in captured
    assert "demo/demo-app:1.2.3" in captured
