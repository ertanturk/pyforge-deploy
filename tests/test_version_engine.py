"""Tests for the version_engine module."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyforge_deploy.builders.version_engine import (
    calculate_next_version,
    fetch_latest_version,
    find_project_root,
    get_cache_path,
    get_dynamic_version,
    get_project_details,
    read_local_version,
    write_both_caches,
    write_version_cache,
)


def test_calculate_next_version() -> None:
    assert calculate_next_version("1.2.3", "patch") == "1.2.4"
    assert calculate_next_version("1.2.3", "minor") == "1.3.0"
    assert calculate_next_version("1.2.3", "major") == "2.0.0"

    with pytest.raises(ValueError, match="Cannot auto-increment malformed"):
        calculate_next_version("invalid", "patch")

    with pytest.raises(ValueError, match="bump_type must be"):
        calculate_next_version("1.0.0", "unknown")


def test_find_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    sub_dir = root / "src" / "package"
    sub_dir.mkdir(parents=True)
    (root / "pyproject.toml").touch()

    assert find_project_root(str(sub_dir)) == str(root)

    monkeypatch.setattr("os.getcwd", lambda: str(tmp_path))
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert find_project_root(str(empty_dir)) == str(tmp_path)


def test_get_project_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[project]\nname = "test-pkg"\nversion = "2.0.0"\n', encoding="utf-8"
    )

    def fake_find_project_root(x: str) -> str:
        return str(tmp_path)

    monkeypatch.setattr(
        "pyforge_deploy.builders.version_engine.find_project_root",
        fake_find_project_root,
    )
    monkeypatch.setattr("os.getcwd", lambda: str(tmp_path))

    name, version = get_project_details()
    assert name == "test-pkg"
    assert version == "2.0.0"

    pyproject_path.unlink()
    with pytest.raises(FileNotFoundError):
        get_project_details()


def test_get_cache_path(tmp_path: Path) -> None:
    cache = tmp_path / ".version_cache"
    about_dir = tmp_path / "src" / "test_pkg"
    about_dir.mkdir(parents=True)
    about = about_dir / "__about__.py"

    # Neither exists
    assert get_cache_path(str(tmp_path), "test-pkg") == str(cache)

    # Only about exists
    about.touch()
    assert get_cache_path(str(tmp_path), "test-pkg") == str(about)


def test_write_caches(tmp_path: Path) -> None:
    cache_path = tmp_path / ".version_cache"
    write_version_cache(str(cache_path), "1.0.0")
    assert cache_path.read_text(encoding="utf-8") == "1.0.0"

    write_both_caches(str(tmp_path), "my-pkg", "1.1.0")
    assert cache_path.read_text(encoding="utf-8") == "1.1.0"
    about_file = tmp_path / "src" / "my_pkg" / "__about__.py"
    assert '__version__ = "1.1.0"' in about_file.read_text(encoding="utf-8")


def test_fetch_latest_version(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"info": {"version": "3.1.4"}}).encode(
        "utf-8"
    )

    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value = mock_response
    monkeypatch.setattr("pyforge_deploy.builders.version_engine.urlopen", mock_urlopen)

    assert fetch_latest_version("dummy-pkg") == "3.1.4"


def test_get_dynamic_version_manual(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "pyforge_deploy.builders.version_engine.get_project_details",
        lambda: ("pkg", "dynamic"),
    )

    def fake_find_project_root(x: str) -> str:
        return str(tmp_path)

    monkeypatch.setattr(
        "pyforge_deploy.builders.version_engine.find_project_root",
        fake_find_project_root,
    )

    version = get_dynamic_version(MANUAL_VERSION="5.0.0")
    assert version == "5.0.0"
    assert (tmp_path / ".version_cache").read_text(encoding="utf-8") == "5.0.0"


def test_read_local_version_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "notfound.txt"
    assert read_local_version(str(missing)) is None


def test_read_local_version_malformed(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.txt"
    malformed.write_text("not a version string", encoding="utf-8")
    assert read_local_version(str(malformed)) is None


def test_write_version_cache_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typing import Any

    def fake_open(*args: Any, **kwargs: Any) -> None:
        raise OSError("fail")

    monkeypatch.setattr("builtins.open", fake_open)
    # Should not raise
    write_version_cache(str(tmp_path / "fail.txt"), "1.2.3")


def test_write_both_caches_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typing import Any

    def fake_open(*args: Any, **kwargs: Any) -> None:
        raise OSError("fail")

    monkeypatch.setattr("builtins.open", fake_open)
    # Should not raise
    write_both_caches(str(tmp_path), "pkg", "1.2.3")


def test_write_both_caches_makedirs_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typing import Any

    def fake_makedirs(*args: Any, **kwargs: Any) -> None:
        raise OSError("fail")

    monkeypatch.setattr("os.makedirs", fake_makedirs)
    # Should not raise
    write_both_caches(str(tmp_path), "pkg", "1.2.3")


def test_get_dynamic_version_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pyforge_deploy.builders.version_engine.get_project_details",
        lambda: (_ for _ in ()).throw(Exception("fail")),
    )
    assert get_dynamic_version() == "0.0.0"


def test_get_dynamic_version_version_compare_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "pyforge_deploy.builders.version_engine.get_project_details",
        lambda: ("pkg", "dynamic"),
    )

    def fake_find_project_root(x: str) -> str:
        return str(tmp_path)

    monkeypatch.setattr(
        "pyforge_deploy.builders.version_engine.find_project_root",
        fake_find_project_root,
    )
    # Write malformed cached version
    (tmp_path / ".version_cache").write_text("notaversion", encoding="utf-8")

    def fake_fetch_latest_version(name: str) -> str:
        return "1.2.3"

    monkeypatch.setattr(
        "pyforge_deploy.builders.version_engine.fetch_latest_version",
        fake_fetch_latest_version,
    )
    assert get_dynamic_version() == "1.2.3"
