import json
from unittest import mock

import pytest

from pyforge_deploy.builders.version_engine import (
    calculate_next_version,
    fetch_latest_version,
    get_dynamic_version,
    get_project_details,
)


def test_calculate_next_version_patch() -> None:
    assert calculate_next_version("1.2.3", "patch") == "1.2.4"


def test_calculate_next_version_minor() -> None:
    assert calculate_next_version("1.2.3", "minor") == "1.3.0"


def test_calculate_next_version_major() -> None:
    assert calculate_next_version("1.2.3", "major") == "2.0.0"


def test_calculate_next_version_invalid() -> None:
    with pytest.raises(ValueError, match="Cannot auto-increment malformed version"):
        calculate_next_version("bad.version", "patch")


@mock.patch("src.pyforge_deploy.builders.version_engine.write_both_caches")
@mock.patch(
    "src.pyforge_deploy.builders.version_engine.get_project_path",
    return_value="/fake/path",
)
@mock.patch(
    "src.pyforge_deploy.builders.version_engine.get_project_details",
    return_value=("proj", "dynamic"),
)
def test_get_dynamic_version_manual(
    mock_details: mock.MagicMock,
    mock_path: mock.MagicMock,
    mock_write: mock.MagicMock,
) -> None:
    assert get_dynamic_version(MANUAL_VERSION="2.0.0") == "2.0.0"
    mock_write.assert_called_once_with("/fake/path", "proj", "2.0.0")


@mock.patch("src.pyforge_deploy.builders.version_engine.write_both_caches")
@mock.patch(
    "src.pyforge_deploy.builders.version_engine.get_project_path",
    return_value="/fake/path",
)
@mock.patch(
    "src.pyforge_deploy.builders.version_engine.fetch_latest_version",
    return_value="1.2.3",
)
@mock.patch(
    "src.pyforge_deploy.builders.version_engine.read_local_version",
    return_value="1.2.3",
)
@mock.patch(
    "src.pyforge_deploy.builders.version_engine.get_project_details",
    return_value=("proj", "dynamic"),
)
def test_get_dynamic_version_bump(
    mock_details: mock.MagicMock,
    mock_read: mock.MagicMock,
    mock_fetch: mock.MagicMock,
    mock_path: mock.MagicMock,
    mock_write: mock.MagicMock,
) -> None:
    assert get_dynamic_version(BUMP_TYPE="patch", AUTO_INCREMENT=True) == "1.2.4"
    mock_write.assert_called_once_with("/fake/path", "proj", "1.2.4")


@mock.patch(
    "src.pyforge_deploy.builders.version_engine.get_project_details",
    return_value=("proj", "1.2.3"),
)
def test_get_dynamic_version_static(mock_details: mock.MagicMock) -> None:
    assert get_dynamic_version() == "1.2.3"


@mock.patch("src.pyforge_deploy.builders.version_engine.urlopen")
def test_fetch_latest_version(mock_urlopen: mock.MagicMock) -> None:
    # Setup the mock to simulate context manager behavior and HTTP response
    mock_response = mock.MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"info": {"version": "1.2.3"}}).encode(
        "utf-8"
    )

    mock_urlopen.return_value.__enter__.return_value = mock_response

    assert fetch_latest_version("proj") == "1.2.3"


@mock.patch(
    "src.pyforge_deploy.builders.version_engine.os.path.exists", return_value=True
)
@mock.patch(
    "src.pyforge_deploy.builders.version_engine.get_pyproject_path",
    return_value="fake_path",
)
@mock.patch("src.pyforge_deploy.builders.version_engine.toml.load")
def test_get_project_details(
    mock_toml_load: mock.MagicMock,
    mock_get_path: mock.MagicMock,
    mock_exists: mock.MagicMock,
) -> None:
    mock_toml_load.return_value = {"project": {"name": "proj", "version": "1.2.3"}}
    assert get_project_details() == ("proj", "1.2.3")
