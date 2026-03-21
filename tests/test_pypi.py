"""Tests for the pypi distribution module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyforge_deploy.builders import pypi as pypi_mod
from pyforge_deploy.builders.pypi import PyPIDistributor
from pyforge_deploy.errors import PyPIDeployError


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
    monkeypatch.delenv("PYPI_TOKEN", raising=False)
    monkeypatch.setattr(pypi_mod, "load_dotenv", lambda **kw: None)

    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)

    # Protect against actual subprocess execution
    monkeypatch.setattr(subprocess, "run", MagicMock())

    dist = PyPIDistributor(dry_run=False)
    dist.token = None

    from pyforge_deploy.errors import ValidationError

    with pytest.raises(ValidationError, match="PYPI_TOKEN is required"):
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

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_token":
            return "fake-token"
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return False
        if tool_key == "pypi_skip_preflight":
            return True
        if tool_key == "pypi_retries":
            return 1
        if tool_key == "pypi_backoff":
            return 1
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

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


def test_pypi_deploy_status_bar_steps(
    mock_pypi_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PyPI deploy should emit five status bar stages in normal flow."""
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", lambda **kw: "1.0.0")

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_token":
            return "fake-token"
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return True
        if tool_key == "pypi_skip_preflight":
            return True
        if tool_key == "pypi_retries":
            return 1
        if tool_key == "pypi_backoff":
            return 1
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)
    monkeypatch.setattr(subprocess, "run", MagicMock())

    calls: list[tuple[int, int, str]] = []

    def fake_status_bar(
        current: int, total: int, message: str, **kwargs: object
    ) -> None:
        calls.append((current, total, message))

    monkeypatch.setattr("pyforge_deploy.builders.pypi.status_bar", fake_status_bar)

    dist = PyPIDistributor()
    dist.token = "fake-token"
    dist.deploy()

    assert calls == [
        (1, 5, "Authenticating PyPI deployment"),
        (2, 5, "Resolving version and deployment options"),
        (3, 5, "Running PyPI preflight checks"),
        (4, 5, "Preparing distribution artifacts"),
        (5, 5, "Uploading distribution to repository"),
    ]


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

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_token":
            return "fake-token"
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return False
        if tool_key == "pypi_skip_preflight":
            return True
        if tool_key == "pypi_retries":
            return 1
        if tool_key == "pypi_backoff":
            return 1
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

    dist = PyPIDistributor(target_version="0.0.0")
    dist.token = "fake-token"
    monkeypatch.setattr(dist, "_clean_dist", lambda: None)
    with pytest.raises(PyPIDeployError, match="Build failed"):
        dist.deploy()


def test_pypi_deploy_build_failure(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", fake_get_dynamic_version_one)

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_token":
            return "fake-token"
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return False
        if tool_key == "pypi_skip_preflight":
            return True
        if tool_key == "pypi_retries":
            return 1
        if tool_key == "pypi_backoff":
            return 1
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

    dist = PyPIDistributor()
    dist.token = "fake-token"
    monkeypatch.setattr(dist, "_clean_dist", lambda: None)
    monkeypatch.setattr(subprocess, "run", fake_run_fail)
    with pytest.raises(PyPIDeployError, match="Build failed"):
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

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_token":
            return "fake-token"
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return False
        if tool_key == "pypi_skip_preflight":
            return True
        if tool_key == "pypi_retries":
            return 1
        if tool_key == "pypi_backoff":
            return 1
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

    call_count = {"build": 0, "twine": 0}

    def fake_run(args: list[str], **kwargs: object) -> None:
        if "twine" in args:
            raise subprocess.CalledProcessError(1, "twine")
        call_count["build"] += 1
        (dist.base_dir / "dist").mkdir(exist_ok=True)
        (dist.base_dir / "dist" / "package-1.0.0.whl").touch(exist_ok=True)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(PyPIDeployError, match="Upload failed"):
        dist.deploy()
    assert call_count["build"] == 1


def test_pypi_skip_preflight_fast_mode(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fast mode should skip preflight and reuse existing dist artifacts."""
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", lambda **kw: "1.0.0")

    preflight_called = {"value": False}

    def fake_preflight(project_name: str, version: str) -> None:
        preflight_called["value"] = True

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_skip_preflight":
            return True
        if tool_key == "pypi_reuse_dist":
            return True
        if tool_key == "pypi_build_target":
            return "both"
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)

    dist = PyPIDistributor()
    dist.token = "fake-token"
    monkeypatch.setattr(dist, "_pre_flight_check", fake_preflight)

    dist.deploy()

    assert preflight_called["value"] is False
    # Reused dist means upload only (no build invocation)
    assert mock_run.call_count == 1
    upload_cmd = mock_run.call_args_list[0][0][0]
    assert "twine" in upload_cmd


def test_pypi_build_target_wheel_uses_wheel_build(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wheel-only mode should invoke build with --wheel."""
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", lambda **kw: "1.0.0")

    dist = PyPIDistributor()
    dist.token = "fake-token"

    # Ensure clean+build path is used by removing pre-created dist artifact.
    for f in (dist.base_dir / "dist").glob("*"):
        f.unlink()

    monkeypatch.setattr(dist, "_pre_flight_check", lambda *_: None)

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_build_target":
            return "wheel"
        if tool_key == "pypi_reuse_dist":
            return False
        if tool_key == "pypi_skip_preflight":
            return False
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(cmd)
        # Simulate build output artifact.
        if len(cmd) >= 3 and cmd[1:3] == ["-m", "build"]:
            (dist.base_dir / "dist").mkdir(exist_ok=True)
            (dist.base_dir / "dist" / "package-1.0.0.whl").touch()
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    dist.deploy()

    build_cmd = calls[0]
    assert build_cmd[1:3] == ["-m", "build"]
    assert "--wheel" in build_cmd
    assert any("twine" in cmd for cmd in calls)


def test_pypi_reuse_dist_skips_build_command(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When reuse_dist is enabled and artifacts exist, build should be skipped."""
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", lambda **kw: "1.0.0")

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return True
        if tool_key == "pypi_skip_preflight":
            return True
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    dist = PyPIDistributor()
    dist.token = "fake-token"
    dist.deploy()

    assert len(calls) == 1
    assert "twine" in calls[0]


def test_pypi_string_false_flags_do_not_enable_fast_paths(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """String 'false' settings should not be treated as True."""
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", lambda **kw: "1.0.0")

    preflight_called = {"value": False}

    def fake_preflight(project_name: str, version: str) -> None:
        preflight_called["value"] = True

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return "false"
        if tool_key == "pypi_skip_preflight":
            return "false"
        if tool_key == "pypi_retries":
            return 1
        if tool_key == "pypi_backoff":
            return 1
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(cmd)
        if len(cmd) >= 3 and cmd[1:3] == ["-m", "build"]:
            (mock_pypi_env / "dist").mkdir(exist_ok=True)
            (mock_pypi_env / "dist" / "package-1.0.0.whl").touch(exist_ok=True)
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    dist = PyPIDistributor()
    dist.token = "fake-token"
    monkeypatch.setattr(dist, "_pre_flight_check", fake_preflight)
    for f in (dist.base_dir / "dist").glob("*"):
        f.unlink()

    dist.deploy()

    assert preflight_called["value"] is True
    assert any(len(cmd) >= 3 and cmd[1:3] == ["-m", "build"] for cmd in calls)


def test_pypi_invalid_retry_and_backoff_values_fall_back_defaults(
    mock_pypi_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid retry/backoff settings should not crash deploy flow."""
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", lambda **kw: "1.0.0")

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return True
        if tool_key == "pypi_skip_preflight":
            return True
        if tool_key == "pypi_retries":
            return "not-an-int"
        if tool_key == "pypi_backoff":
            return "also-bad"
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)
    monkeypatch.setattr(subprocess, "run", MagicMock())

    dist = PyPIDistributor()
    dist.token = "fake-token"

    # Should not raise due to int conversion failure in settings.
    dist.deploy()


def test_pypi_dry_run_without_token_skips_auth_and_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run should not require PYPI_TOKEN and should not execute subprocesses."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.delenv("PYPI_TOKEN", raising=False)
    monkeypatch.setattr(pypi_mod, "load_dotenv", lambda **kw: None)
    monkeypatch.setattr(pypi_mod, "get_dynamic_version", lambda **kw: "1.2.3")

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_token":
            return None
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return False
        if tool_key == "pypi_skip_preflight":
            return False
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)

    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)

    dist = PyPIDistributor(dry_run=True, verbose=True)
    dist.token = None
    dist.deploy()

    out = capsys.readouterr().out
    assert "[DRY RUN] Deployment simulation successful" in out
    assert mock_run.call_count == 0


def test_pypi_tag_release_keeps_version_cache_writes_enabled(
    mock_pypi_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tag-based CI deploy should keep cache writes for build version consistency."""
    captured_kwargs: dict[str, object] = {}

    def fake_get_dynamic_version(**kwargs: object) -> str:
        captured_kwargs.update(kwargs)
        return "1"

    monkeypatch.setattr(pypi_mod, "get_dynamic_version", fake_get_dynamic_version)
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v1")

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "pypi_token":
            return "fake-token"
        if tool_key == "pypi_build_target":
            return "both"
        if tool_key == "pypi_reuse_dist":
            return True
        if tool_key == "pypi_skip_preflight":
            return True
        if tool_key == "pypi_retries":
            return 1
        if tool_key == "pypi_backoff":
            return 1
        return default

    monkeypatch.setattr(pypi_mod, "resolve_setting", fake_resolve_setting)
    monkeypatch.setattr(subprocess, "run", MagicMock())

    dist = PyPIDistributor(target_version="1", verbose=True)
    dist.token = "fake-token"
    dist.deploy()

    assert captured_kwargs.get("WRITE_CACHE") is True
