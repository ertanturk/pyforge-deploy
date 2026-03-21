"""CLI integration tests for plugin hook injection points."""

from __future__ import annotations

import sys

import pytest

import pyforge_deploy.cli as cli_mod


def test_cli_docker_runs_before_and_after_build_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker command should execute build hooks around deployment."""

    class _DockerBuilder:
        def __init__(
            self, entry_point: str | None = None, image_tag: str | None = None
        ):
            self.entry_point = entry_point
            self.image_tag = image_tag

        def deploy(self, push: bool = False) -> None:
            return None

    hook_calls: list[str] = []

    monkeypatch.setattr(
        cli_mod, "run_hooks", lambda stage, verbose=False: hook_calls.append(stage)
    )
    monkeypatch.setattr(cli_mod, "DockerBuilder", _DockerBuilder)
    monkeypatch.setattr(
        sys, "argv", ["pyforge-deploy", "docker-build", "--image-tag", "demo/app:1.0.0"]
    )

    cli_mod.main()

    assert hook_calls == ["before_build", "after_build"]


def test_cli_pypi_runs_before_and_after_release_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyPI command should execute release hooks around deployment."""

    class _PyPIDistributor:
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
            return None

    hook_calls: list[str] = []

    monkeypatch.setattr(
        cli_mod, "run_hooks", lambda stage, verbose=False: hook_calls.append(stage)
    )
    monkeypatch.setattr(cli_mod, "PyPIDistributor", _PyPIDistributor)
    monkeypatch.setattr(
        sys, "argv", ["pyforge-deploy", "deploy-pypi", "--bump", "shame"]
    )

    cli_mod.main()

    assert hook_calls == ["before_release", "after_release"]
