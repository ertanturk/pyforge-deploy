"""Tests for the version_engine module."""

import builtins
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import pyforge_deploy.builders.version_engine as version_mod
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

    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
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

    monkeypatch.setattr(version_mod, "find_project_root", fake_find_project_root)
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))

    name, version = get_project_details()
    assert name == "test-pkg"
    assert version == "2.0.0"

    pyproject_path.unlink()
    with pytest.raises(FileNotFoundError):
        get_project_details()


def test_get_cache_path(tmp_path: Path) -> None:
    cache = tmp_path / ".pyforge-deploy-cache" / "version_cache"
    legacy = tmp_path / ".version_cache"

    # Neither exists, canonical is returned
    assert get_cache_path(str(tmp_path), "test-pkg") == str(cache)

    # Legacy fallback still works for migration compatibility
    legacy.write_text("1.0.0", encoding="utf-8")
    assert get_cache_path(str(tmp_path), "test-pkg") == str(legacy)

    # Canonical path takes precedence when available
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("1.0.1", encoding="utf-8")
    assert get_cache_path(str(tmp_path), "test-pkg") == str(cache)


def test_write_caches(tmp_path: Path) -> None:
    cache_path = tmp_path / ".pyforge-deploy-cache" / "version_cache"
    write_version_cache(str(cache_path), "1.0.0")
    assert cache_path.read_text(encoding="utf-8") == "1.0.0"

    write_both_caches(str(tmp_path), "my-pkg", "1.1.0")
    assert cache_path.read_text(encoding="utf-8") == "1.1.0"


def test_fetch_latest_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"info": {"version": "3.1.4"}}).encode(
        "utf-8"
    )

    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value = mock_response
    monkeypatch.setattr(version_mod, "urlopen", mock_urlopen)
    monkeypatch.setattr(version_mod, "get_project_path", lambda: str(tmp_path))
    version_mod._PYPI_CACHE.clear()

    assert fetch_latest_version("dummy-pkg") == "3.1.4"


def test_fetch_latest_version_reads_from_disk_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fetch should use persistent cache when fresh and skip network."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    cache_dir = tmp_path / ".pyforge-deploy-cache"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "pypi_network_cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "dummy-pkg": {
                    "version": "9.9.9",
                    "fetched_at": 9999999999,
                }
            }
        ),
        encoding="utf-8",
    )

    version_mod._PYPI_CACHE.clear()
    monkeypatch.setattr(version_mod, "get_project_path", lambda: str(tmp_path))
    monkeypatch.setattr(version_mod, "urlopen", MagicMock(side_effect=AssertionError))

    assert fetch_latest_version("dummy-pkg") == "9.9.9"


def test_fetch_latest_version_writes_disk_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful network fetch should persist result to disk cache."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"info": {"version": "1.2.3"}}).encode(
        "utf-8"
    )
    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value = mock_response

    version_mod._PYPI_CACHE.clear()
    monkeypatch.setattr(version_mod, "get_project_path", lambda: str(tmp_path))
    monkeypatch.setattr(version_mod, "urlopen", mock_urlopen)

    assert fetch_latest_version("cached-pkg") == "1.2.3"

    cache_file = tmp_path / ".pyforge-deploy-cache" / "pypi_network_cache.json"
    assert cache_file.exists()
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    assert payload["cached-pkg"]["version"] == "1.2.3"


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
    assert (tmp_path / ".pyforge-deploy-cache" / "version_cache").read_text(
        encoding="utf-8"
    ) == "5.0.0"


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

    monkeypatch.setattr(builtins, "open", fake_open)
    # Should not raise
    write_version_cache(str(tmp_path / "fail.txt"), "1.2.3")


def test_write_both_caches_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typing import Any

    def fake_open(*args: Any, **kwargs: Any) -> None:
        raise OSError("fail")

    monkeypatch.setattr(builtins, "open", fake_open)
    # Should not raise
    write_both_caches(str(tmp_path), "pkg", "1.2.3")


def test_write_both_caches_makedirs_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typing import Any

    def fake_makedirs(*args: Any, **kwargs: Any) -> None:
        raise OSError("fail")

    monkeypatch.setattr(os, "makedirs", fake_makedirs)
    # Should not raise
    write_both_caches(str(tmp_path), "pkg", "1.2.3")


def test_get_dynamic_version_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        version_mod,
        "get_project_details",
        lambda: (_ for _ in ()).throw(Exception("fail")),
    )
    assert get_dynamic_version() == "0.0.0"


def test_get_dynamic_version_version_compare_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(version_mod, "get_project_details", lambda: ("pkg", "dynamic"))

    def fake_find_project_root(x: str) -> str:
        return str(tmp_path)

    monkeypatch.setattr(version_mod, "find_project_root", fake_find_project_root)
    # Write malformed cached version
    version_cache = tmp_path / ".pyforge-deploy-cache" / "version_cache"
    version_cache.parent.mkdir(parents=True, exist_ok=True)
    version_cache.write_text("notaversion", encoding="utf-8")

    def fake_fetch_latest_version(name: str) -> str:
        return "1.2.3"

    monkeypatch.setattr(version_mod, "fetch_latest_version", fake_fetch_latest_version)
    assert get_dynamic_version() == "1.2.3"


def test_get_dynamic_version_dry_run_still_fetches_pypi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dry-run version resolution should still read current PyPI version."""
    monkeypatch.setattr(version_mod, "get_project_details", lambda: ("pkg", "dynamic"))

    def fake_find_project_root(x: str) -> str:
        return str(tmp_path)

    monkeypatch.setattr(version_mod, "find_project_root", fake_find_project_root)
    version_cache = tmp_path / ".pyforge-deploy-cache" / "version_cache"
    version_cache.parent.mkdir(parents=True, exist_ok=True)
    version_cache.write_text("1.2.3", encoding="utf-8")
    monkeypatch.setattr(
        version_mod,
        "fetch_latest_version",
        lambda *_args, **_kwargs: "2.4.5",
    )

    assert get_dynamic_version(DRY_RUN=True) == "2.4.5"


def test_get_dynamic_version_manual_write_cache_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Manual version should not write cache files when WRITE_CACHE is False."""
    monkeypatch.setattr(version_mod, "get_project_details", lambda: ("pkg", "dynamic"))
    monkeypatch.setattr(version_mod, "find_project_root", lambda _x: str(tmp_path))

    result = get_dynamic_version(MANUAL_VERSION="2.0.0", WRITE_CACHE=False)

    assert result == "2.0.0"
    assert not (tmp_path / ".pyforge-deploy-cache" / "version_cache").exists()


def test_get_dynamic_version_static_version_respects_explicit_bump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Static project versions should not block explicit bump requests."""
    monkeypatch.setattr(version_mod, "get_project_details", lambda: ("pkg", "1.0.0"))
    monkeypatch.setattr(version_mod, "find_project_root", lambda _x: str(tmp_path))
    monkeypatch.setattr(
        version_mod, "fetch_latest_version", lambda *_args, **_kwargs: None
    )

    result = get_dynamic_version(BUMP_TYPE="default")

    assert result == "1.1.0"


def test_suggest_bump_from_git_uses_latest_tag_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git bump analysis should scope commit inspection to latest tag..HEAD."""
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/git")

    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        check: bool = False,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        calls.append(cmd)
        if cmd[:4] == ["/usr/bin/git", "describe", "--tags", "--abbrev=0"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="v2.0.0\n", stderr="")
        if cmd[:2] == ["/usr/bin/git", "log"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="fix: patch hotfix\n\n---COMMIT_SEP---\n",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert version_mod.suggest_bump_from_git() == "shame"
    assert len(calls) >= 2
    assert calls[1][0:3] == ["/usr/bin/git", "log", "v2.0.0..HEAD"]


def test_fetch_latest_version_handles_404_as_initial_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """404 from PyPI should be treated as initial release, not failure."""
    from urllib.error import HTTPError

    version_mod._PYPI_CACHE.clear()
    monkeypatch.setattr(version_mod, "get_project_path", lambda: str(tmp_path))

    captured_logs: list[tuple[str, str]] = []

    def fake_log(message: str, color: str = "blue") -> None:
        captured_logs.append((message, color))

    monkeypatch.setattr(version_mod, "_log", fake_log)

    def raise_404(*_args: object, **_kwargs: object) -> object:
        raise HTTPError(
            url="https://pypi.org/pypi/new-pkg/json",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(version_mod, "urlopen", raise_404)

    assert fetch_latest_version("new-pkg") is None
    assert any("Assuming initial release" in msg for msg, _ in captured_logs)


def test_get_dynamic_version_uses_git_release_floor_when_cache_is_behind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Latest merged git release tag should prevent reusing already released version."""
    monkeypatch.setattr(version_mod, "get_project_details", lambda: ("pkg", "dynamic"))
    monkeypatch.setattr(version_mod, "find_project_root", lambda _x: str(tmp_path))
    monkeypatch.setattr(
        version_mod,
        "fetch_latest_version",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        version_mod,
        "fetch_latest_git_version",
        lambda _project_path: "1.2.9",
    )

    cache_path = tmp_path / ".pyforge-deploy-cache" / "version_cache"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("1.2.8", encoding="utf-8")

    result = get_dynamic_version(BUMP_TYPE="shame")
    assert result == "1.2.10"
