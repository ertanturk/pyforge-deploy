"""Additional CLI coverage tests for helper and error branches."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

import pyforge_deploy.cli as cli_mod
from pyforge_deploy.errors import PyForgeError


class _Response:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_cli_helpers_cover_release_and_docker_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise git, GitHub, and Docker Hub helper branches."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert cli_mod._get_last_release_tag() == "Unavailable (git not found)"

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_tag(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="v1.2.3\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_tag)
    assert cli_mod._get_last_release_tag() == "v1.2.3"

    def fake_run_fail(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fail")

    monkeypatch.setattr(subprocess, "run", fake_run_fail)
    assert cli_mod._get_last_release_tag() == "None"

    def fake_run_slug(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="git@github.com:owner/repo.git\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run_slug)
    assert cli_mod._get_github_repo_slug() == "owner/repo"

    def fake_run_slug_https(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="https://github.com/owner/repo.git\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run_slug_https)
    assert cli_mod._get_github_repo_slug() == "owner/repo"

    assert cli_mod._get_last_release_published_at("None") == "N/A"

    monkeypatch.setattr(cli_mod, "_get_github_repo_slug", lambda: "owner/repo")

    def fake_urlopen(api_url: object, timeout: float = 5) -> _Response:
        return _Response({"published_at": "2026-03-19T10:00:00Z"})

    monkeypatch.setattr(cli_mod, "urlopen", fake_urlopen)
    assert "UTC" in cli_mod._get_last_release_published_at("v1.2.3")

    def fake_urlopen_fail(api_url: object, timeout: float = 5) -> _Response:
        raise URLError("network")

    monkeypatch.setattr(cli_mod, "urlopen", fake_urlopen_fail)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_date(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="2026-03-19T10:00:00+00:00\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run_date)
    assert "UTC" in cli_mod._get_last_release_published_at("v1.2.3")

    assert cli_mod._check_docker_image_status(None) == "Not configured"
    assert cli_mod._check_docker_image_status(" ") == "Not configured"
    assert cli_mod._check_docker_image_status("ghcr.io/org/app:1") == (
        "Skipped (non-Docker Hub registry)"
    )

    def fake_urlopen_exists(api_url: object, timeout: float = 5) -> _Response:
        return _Response({}, status=200)

    monkeypatch.setattr(cli_mod, "urlopen", fake_urlopen_exists)
    assert cli_mod._check_docker_image_status("owner/repo:latest") == "Exists"

    def fake_urlopen_not_found(api_url: object, timeout: float = 5) -> _Response:
        raise HTTPError(str(api_url), 404, "not found", hdrs=None, fp=None)

    monkeypatch.setattr(cli_mod, "urlopen", fake_urlopen_not_found)
    assert cli_mod._check_docker_image_status("owner/repo:latest") == "Not found"

    def fake_urlopen_network(api_url: object, timeout: float = 5) -> _Response:
        raise URLError("network")

    monkeypatch.setattr(cli_mod, "urlopen", fake_urlopen_network)
    assert cli_mod._check_docker_image_status("owner/repo:latest") == (
        "Unavailable (network)"
    )


def test_cli_init_updates_existing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise init flow when project files already exist."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "init"])
    monkeypatch.setattr(cli_mod, "get_project_details", lambda: ("demo-app", "1.0.0"))
    monkeypatch.setattr(
        cli_mod, "detect_entry_point", lambda _path: "src/demo_app/cli.py"
    )
    monkeypatch.setattr(
        cli_mod, "list_potential_entry_points", lambda _path: ["src/demo_app/cli.py"]
    )
    monkeypatch.setattr(
        cli_mod,
        "detect_dependencies",
        lambda _path: {
            "has_pyproject": True,
            "requirement_files": ["requirements.txt"],
        },
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_file = workflow_dir / "pyforge-deploy.yml"
    workflow_file.write_text(
        cli_mod.GITHUB_RELEASE_YAML.strip() + "\n", encoding="utf-8"
    )

    dockerignore = tmp_path / ".dockerignore"
    dockerignore.write_text(".git\n", encoding="utf-8")

    env_example = tmp_path / ".env.example"
    env_example.write_text("PYPI_TOKEN=\n", encoding="utf-8")

    cache_dir = tmp_path / ".pyforge-deploy-cache"
    cache_dir.mkdir()

    (cache_dir / "version_cache").write_text("1.0.0", encoding="utf-8")

    main_result = cli_mod.main()
    assert main_result is None

    out = capsys.readouterr().out
    assert "Workflow is already up-to-date" in out
    assert "already exists" in out


def test_cli_main_handles_domain_and_generic_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the top-level PyForgeError and generic exception handlers."""

    def fake_parse_args_pyforge(self: argparse.ArgumentParser) -> SimpleNamespace:
        return SimpleNamespace(
            func=lambda _args: (_ for _ in ()).throw(PyForgeError("boom"))
        )

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args_pyforge)
    with pytest.raises(SystemExit) as excinfo:
        cli_mod.main()
    assert excinfo.value.code == 2

    def fake_parse_args_generic(self: argparse.ArgumentParser) -> SimpleNamespace:
        return SimpleNamespace(
            func=lambda _args: (_ for _ in ()).throw(ValueError("boom"))
        )

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args_generic)
    with pytest.raises(SystemExit) as excinfo2:
        cli_mod.main()
    assert excinfo2.value.code == 1


def test_cli_handlers_cover_attribute_error_and_default_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover handler branches that fall back on defaults and tolerate attrs."""

    class _DockerBuilder:
        __slots__ = ("entry_point", "image_tag", "push")

        def __init__(
            self, entry_point: str | None = None, image_tag: str | None = None
        ) -> None:
            self.entry_point = entry_point
            self.image_tag = image_tag

        def deploy(self, push: bool = False) -> None:
            self.push = push  # type: ignore[attr-defined]

    class _PyPIDistributor:
        __slots__ = ("target_version", "use_test_pypi", "bump_type", "called")

        def __init__(
            self,
            target_version: str | None = None,
            use_test_pypi: bool = False,
            bump_type: str | None = None,
        ) -> None:
            self.target_version = target_version
            self.use_test_pypi = use_test_pypi
            self.bump_type = bump_type

        def deploy(self) -> None:
            self.called = True  # type: ignore[attr-defined]

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        values: dict[str, object] = {
            "docker_push": "yes",
            "auto_confirm": "true",
            "docker_platforms": "linux/amd64",
            "docker_image": "demo/app:1.0.0",
            "docker_dry_run": "0",
            "verbose": "1",
            "pypi_dry_run": "0",
            "default_bump": "minor",
        }
        return values.get(tool_key, default)

    monkeypatch.setattr(cli_mod, "resolve_setting", fake_resolve_setting)

    monkeypatch.setattr(cli_mod, "DockerBuilder", _DockerBuilder)
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "docker-build", "--push"])
    cli_mod.main()

    monkeypatch.setattr(cli_mod, "PyPIDistributor", _PyPIDistributor)
    monkeypatch.setattr(
        "pyforge_deploy.builders.version_engine.suggest_bump_from_git",
        lambda: (_ for _ in ()).throw(RuntimeError("no git history")),
    )
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "deploy-pypi"])
    cli_mod.main()


def test_cli_entry_point_and_status_edge_cases(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cover empty entry-point discovery and status tip/warning branches."""
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "show-entry-point"])
    monkeypatch.setattr(cli_mod, "detect_entry_point", lambda _path: None)
    monkeypatch.setattr(cli_mod, "list_potential_entry_points", lambda _path: [])
    cli_mod.main()
    out = capsys.readouterr().out
    assert "No entry point detected" in out
    assert "No entry points found" in out

    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "status"])
    monkeypatch.setattr(cli_mod, "get_project_details", lambda: ("demo-app", "1.0.0"))
    monkeypatch.setattr(cli_mod, "get_dynamic_version", lambda: "1.0.0")
    monkeypatch.setattr(cli_mod, "fetch_latest_version", lambda _name: "1.0.0")
    monkeypatch.setattr(cli_mod, "_get_last_release_tag", lambda: "v1.0.0")
    monkeypatch.setattr(
        cli_mod, "_get_last_release_published_at", lambda _tag: "2026-03-19 10:00 UTC"
    )
    monkeypatch.setattr(cli_mod, "_check_docker_image_status", lambda _tag: "Exists")

    def fake_resolve_setting_status(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        values: dict[str, object] = {
            "pypi_token": "token",
            "docker_user": "demo",
            "docker_image": "demo/demo-app:1.0.0",
        }
        return values.get(tool_key, default)

    monkeypatch.setattr(cli_mod, "resolve_setting", fake_resolve_setting_status)
    cli_mod.main()
    out2 = capsys.readouterr().out
    assert "Tip: Your local version matches PyPI" in out2
    assert "Warning: PYPI_TOKEN is not set" not in out2


def test_cli_docker_uses_config_auto_confirm_when_yes_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker handler should honor config/env auto-confirm when --yes is omitted."""

    class _DockerBuilderCapture:
        last_instance: _DockerBuilderCapture | None = None

        def __init__(
            self, entry_point: str | None = None, image_tag: str | None = None
        ) -> None:
            self.entry_point = entry_point
            self.image_tag = image_tag
            self.verbose = False
            self.auto_confirm = False
            self.dry_run = False
            self.platforms = None
            _DockerBuilderCapture.last_instance = self

        def deploy(self, push: bool = False) -> None:
            self.push = push

    seen_cli_values: dict[str, object] = {}

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "auto_confirm":
            seen_cli_values["auto_confirm"] = cli_value
            if cli_value is not None:
                return cli_value
            return True
        if tool_key == "docker_push":
            return False if cli_value is None else cli_value
        if tool_key == "docker_image":
            return "demo/app:1.0.0"
        if tool_key == "docker_dry_run":
            return False if cli_value is None else cli_value
        if tool_key == "verbose":
            return False if cli_value is None else cli_value
        if tool_key == "docker_platforms":
            return None
        return default

    monkeypatch.setattr(cli_mod, "resolve_setting", fake_resolve_setting)
    monkeypatch.setattr(cli_mod, "DockerBuilder", _DockerBuilderCapture)
    monkeypatch.setattr(sys, "argv", ["pyforge-deploy", "docker-build"])

    cli_mod.main()

    assert seen_cli_values["auto_confirm"] is None
    assert _DockerBuilderCapture.last_instance is not None
    assert _DockerBuilderCapture.last_instance.auto_confirm is True
