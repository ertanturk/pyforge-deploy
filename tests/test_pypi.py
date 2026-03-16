"""Tests for the pypi distribution module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyforge_deploy.builders import pypi as pypi_mod
from pyforge_deploy.builders.pypi import PyPIDistributor


@pytest.fixture
def mock_pypi_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setenv("PYPI_TOKEN", "fake-token")

    # Block load_dotenv so it doesn't load real secrets
    def fake_load_dotenv(**kw: object) -> None:
        return None

    monkeypatch.setattr(pypi_mod, "load_dotenv", fake_load_dotenv)

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "package-1.0.0.whl").touch()

    return tmp_path


def test_pypi_init_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicitly clear token to guarantee ValueError
    monkeypatch.delenv("PYPI_TOKEN", raising=False)
    monkeypatch.setattr(pypi_mod, "load_dotenv", lambda **kw: None)

    # Protect against actual subprocess execution
    monkeypatch.setattr(subprocess, "run", MagicMock())

    dist = PyPIDistributor(dry_run=True)
    dist.token = None  # Force None in case env vars leaked

    with pytest.raises(ValueError, match="PYPI_TOKEN is required"):
        dist.deploy()


def test_pypi_clean_dist(mock_pypi_env: Path) -> None:
    dist_dir = mock_pypi_env / "dist"
    egg_info = mock_pypi_env / "test.egg-info"
    egg_info.mkdir()

    dist = PyPIDistributor()
    dist._clean_dist()

    assert not dist_dir.exists()
    assert not egg_info.exists()


def test_pypi_deploy_success(
    mock_pypi_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", lambda **kw: "1.0.0")

    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)

    dist = PyPIDistributor()
    # Bypass clean so mock .whl survives
    monkeypatch.setattr(dist, "_clean_dist", lambda: None)

    dist.deploy()

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0][0][0][1:3] == ["-m", "build"]
    twine_cmd = mock_run.call_args_list[1][0][0]
    assert "twine" in twine_cmd
    assert "upload" in twine_cmd


def fake_get_dynamic_version_zero(**kw: object) -> str:
    return "0.0.0"


def fake_get_dynamic_version_one(**kw: object) -> str:
    return "1.0.0"


def fake_run_none(*a: object, **kw: object) -> None:
    return None


def fake_run_fail(*a: object, **kw: object) -> None:
    raise subprocess.CalledProcessError(1, "build")


def fake_run_twine_fail(args: list[str], **kwargs: object) -> None:
    if "twine" in args:
        raise subprocess.CalledProcessError(1, "twine")


def test_pypi_deploy_invalid_version(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", fake_get_dynamic_version_zero)
    dist = PyPIDistributor(target_version="0.0.0")
    dist.token = "fake-token"
    monkeypatch.setattr(dist, "_clean_dist", lambda: None)
    with pytest.raises(ValueError, match="Invalid version '0.0.0'"):
        dist.deploy()


def test_pypi_deploy_build_failure(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", fake_get_dynamic_version_one)
    dist = PyPIDistributor()
    dist.token = "fake-token"
    monkeypatch.setattr(dist, "_clean_dist", lambda: None)
    monkeypatch.setattr(subprocess, "run", fake_run_fail)
    with pytest.raises(RuntimeError, match="Build failed"):
        dist.deploy()


def test_pypi_deploy_no_dist_files(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", fake_get_dynamic_version_one)
    dist = PyPIDistributor()
    dist.token = "fake-token"
    monkeypatch.setattr(dist, "_clean_dist", lambda: None)
    monkeypatch.setattr(subprocess, "run", fake_run_none)
    dist_dir = dist.base_dir / "dist"
    for f in dist_dir.glob("*"):
        f.unlink()
    with pytest.raises(RuntimeError, match="No distribution files found"):
        dist.deploy()


def test_pypi_deploy_upload_failure(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pyforge_deploy.builders.pypi.get_dynamic_version",
        fake_get_dynamic_version_one,
    )
    dist = PyPIDistributor()
    dist.token = "fake-token"
    monkeypatch.setattr(dist, "_clean_dist", lambda: None)
    call_count = {"build": 0, "twine": 0}

    def fake_run(args: list[str], **kwargs: object) -> None:
        if "twine" in args:
            raise subprocess.CalledProcessError(1, "twine")
        call_count["build"] += 1

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Upload failed"):
        dist.deploy()
    assert call_count["build"] == 1
