"""Tests for the CLI module."""

import sys
from pathlib import Path
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


def test_cli_help_shows_command_center(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Top-level help should show engaging command center sections."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "-h"])

    with pytest.raises(SystemExit):
        main()

    out = capsys.readouterr().out
    assert "Command Center" in out
    assert "Release & Build" in out
    assert "Discovery" in out


def test_cli_docker_help_has_grouped_sections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Docker subcommand help should expose grouped argument menus."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "docker-build", "-h"])

    with pytest.raises(SystemExit):
        main()

    out = capsys.readouterr().out
    assert "Build Inputs" in out
    assert "Execution Mode" in out


def test_cli_pypi_help_has_grouped_sections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PyPI subcommand help should expose grouped argument menus."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "deploy-pypi", "-h"])

    with pytest.raises(SystemExit):
        main()

    out = capsys.readouterr().out
    assert "Release Target" in out
    assert "Execution Mode" in out


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


def test_cli_init_creates_useful_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Init should create workflow, dockerignore, env example and version artifacts."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "init"])
    monkeypatch.setattr(cli_mod, "get_project_details", lambda: ("demo-app", "dynamic"))

    main()

    assert (tmp_path / ".github" / "workflows" / "pyforge-deploy.yml").exists()
    assert (tmp_path / ".dockerignore").exists()
    assert (tmp_path / ".env.example").exists()
    assert (tmp_path / ".pyforge-deploy-cache").exists()
    assert (tmp_path / ".version_cache").exists()
    about = tmp_path / "demo_app" / "__about__.py"
    assert about.exists()
    assert "__version__" in about.read_text(encoding="utf-8")


def test_cli_init_backs_up_existing_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Init should backup custom workflow before replacing with template."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_file = workflow_dir / "pyforge-deploy.yml"
    workflow_file.write_text("name: Custom Workflow\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "init"])
    monkeypatch.setattr(cli_mod, "get_project_details", lambda: ("demo-app", "1.0.0"))

    main()

    backup = workflow_dir / "pyforge-deploy.yml.bak"
    assert backup.exists()
    assert "Custom Workflow" in backup.read_text(encoding="utf-8")
    assert "PyForge Release" in workflow_file.read_text(encoding="utf-8")


def test_cli_init_dockerignore_slash_variants_not_duplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Init should not append duplicate ignores for slash/no-slash variants."""
    dockerignore = tmp_path / ".dockerignore"
    dockerignore.write_text(
        "\n".join(
            [
                ".git",
                ".venv",
                "venv",
                "env",
                "__pycache__",
                "*.pyc",
                "*.pyo",
                "*.pyd",
                ".pytest_cache",
                ".tox",
                "build",
                "dist",
                "*.egg-info",
                ".env",
                "tests",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    before = dockerignore.read_text(encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "init"])
    monkeypatch.setattr(cli_mod, "get_project_details", lambda: ("demo-app", "1.0.0"))

    main()

    after = dockerignore.read_text(encoding="utf-8")
    assert after == before
    assert "# Added by pyforge-deploy init" not in after
