import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import pytest

from pyforge_deploy.builders.pypi import PyPIDistributor


@pytest.fixture  # type: ignore
def mock_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[Path, None, None]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@mock.patch(
    "src.pyforge_deploy.builders.pypi.get_dynamic_version", return_value="1.2.3"
)
@mock.patch("subprocess.run")
def test_deploy_success(
    mock_run: mock.MagicMock, mock_get_version: mock.MagicMock, mock_env: Path
) -> None:
    distributor = PyPIDistributor(
        target_version="1.2.3", use_test_pypi=True, bump_type="patch"
    )
    distributor.token = "dummy-token"

    # Prevent actual directory deletion
    distributor._clean_dist = mock.MagicMock()  # pyright: ignore[reportPrivateUsage]  # type: ignore[method-assign]

    # Create dummy dist files
    dist_dir = distributor.base_dir / "dist"
    dist_dir.mkdir(exist_ok=True)
    (dist_dir / "package.whl").touch()
    (dist_dir / "package.tar.gz").touch()

    distributor.deploy()

    assert mock_run.call_count == 2
    mock_run.assert_any_call(
        [sys.executable, "-m", "build"], check=True, cwd=distributor.base_dir
    )


@mock.patch(
    "src.pyforge_deploy.builders.pypi.get_dynamic_version", return_value="0.0.0"
)
def test_deploy_invalid_version(
    mock_get_version: mock.MagicMock, mock_env: Path
) -> None:
    distributor = PyPIDistributor(target_version="0.0.0")
    distributor.token = "dummy-token"

    with pytest.raises(ValueError, match="Invalid version"):
        distributor.deploy()


@mock.patch(
    "src.pyforge_deploy.builders.pypi.get_dynamic_version", return_value="1.2.3"
)
def test_deploy_missing_token(mock_get_version: mock.MagicMock, mock_env: Path) -> None:
    distributor = PyPIDistributor(target_version="1.2.3")
    distributor.token = None

    with pytest.raises(ValueError, match="PYPI_TOKEN is required"):
        distributor.deploy()


@mock.patch(
    "src.pyforge_deploy.builders.pypi.get_dynamic_version", return_value="1.2.3"
)
@mock.patch("subprocess.run")
def test_deploy_build_failure(
    mock_run: mock.MagicMock, mock_get_version: mock.MagicMock, mock_env: Path
) -> None:
    distributor = PyPIDistributor(target_version="1.2.3")
    distributor.token = "dummy-token"
    distributor._clean_dist = mock.MagicMock()  # pyright: ignore[reportPrivateUsage]  # type: ignore[method-assign]

    mock_run.side_effect = subprocess.CalledProcessError(1, "build")

    with pytest.raises(RuntimeError, match="Build failed"):
        distributor.deploy()


@mock.patch(
    "src.pyforge_deploy.builders.pypi.get_dynamic_version", return_value="1.2.3"
)
@mock.patch("subprocess.run")
def test_deploy_no_dist_files(
    mock_run: mock.MagicMock, mock_get_version: mock.MagicMock, mock_env: Path
) -> None:
    distributor = PyPIDistributor(target_version="1.2.3")
    distributor.token = "dummy-token"
    distributor._clean_dist = mock.MagicMock()  # pyright: ignore[reportPrivateUsage]  # type: ignore[method-assign]

    # Intentionally leaving the dist_dir empty
    with pytest.raises(RuntimeError, match="No distribution files found"):
        distributor.deploy()


@mock.patch(
    "src.pyforge_deploy.builders.pypi.get_dynamic_version", return_value="1.2.3"
)
@mock.patch("subprocess.run")
def test_deploy_upload_failure(
    mock_run: mock.MagicMock, mock_get_version: mock.MagicMock, mock_env: Path
) -> None:
    distributor = PyPIDistributor(target_version="1.2.3")
    distributor.token = "dummy-token"
    distributor._clean_dist = mock.MagicMock()  # pyright: ignore[reportPrivateUsage]  # type: ignore[method-assign]

    dist_dir = distributor.base_dir / "dist"
    dist_dir.mkdir(exist_ok=True)
    (dist_dir / "package.whl").touch()

    # First subprocess call succeeds (build), second call fails (twine upload)
    mock_run.side_effect = [None, subprocess.CalledProcessError(1, "twine")]

    with pytest.raises(RuntimeError, match="Upload failed"):
        distributor.deploy()
