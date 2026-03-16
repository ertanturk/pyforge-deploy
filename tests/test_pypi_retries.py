import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from pyforge_deploy.builders.pypi import PyPIDistributor


def test_pypi_upload_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Prepare minimal project pyproject
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "dummy"\nversion = "0.1.0"\n')
    monkeypatch.chdir(tmp_path)

    # Ensure token present
    monkeypatch.setenv("PYPI_TOKEN", "fake")

    # Create a dist file to be uploaded
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    wheel = dist_dir / "dummy-0.1.0-py3-none-any.whl"
    wheel.write_text("binary")

    calls = {"count": 0}

    def fake_run(
        cmd: str | Sequence[str],
        check: bool = True,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[Any]:
        # Simulate build command success
        if "-m" in cmd and "build" in cmd:
            return subprocess.CompletedProcess(cmd, 0)
        # Simulate upload failures then success
        calls["count"] += 1
        if calls["count"] < 2:
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    distributor = PyPIDistributor(dry_run=False, verbose=True, auto_confirm=True)
    # Monkeypatch cleanup to avoid removing files
    monkeypatch.setattr(distributor, "_cleanup", lambda: None)

    # Should not raise despite initial upload failure (retries)
    distributor.deploy()
