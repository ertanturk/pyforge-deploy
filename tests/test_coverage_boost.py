"""Additional coverage-focused tests for helper branches."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import pyforge_deploy.builders.docker_engine as docker_engine_mod
import pyforge_deploy.builders.parallel as parallel_mod
import pyforge_deploy.builders.version_engine as version_mod
import pyforge_deploy.colors as colors_mod
import pyforge_deploy.config as config_mod


def test_colors_and_ci_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover NO_COLOR, bold=False, and CI detection branches."""
    monkeypatch.setenv("NO_COLOR", "1")
    assert colors_mod.color_text("plain", "red", bold=False) == "plain"

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("CI", "true")
    assert colors_mod.is_ci_environment() is True

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert colors_mod.is_ci_environment() is True


def test_resolve_setting_cast_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure cast failures fall back to the raw environment value."""

    def boom() -> dict[str, object]:
        raise RuntimeError("invalid config")

    monkeypatch.setattr(config_mod, "get_tool_config", boom)
    monkeypatch.setenv("TEST_INT_SETTING", "not-an-int")

    result = config_mod.resolve_setting(
        None,
        "test_int_setting",
        env_keys=("TEST_INT_SETTING",),
        default=7,
        cast=int,
    )

    assert result == "not-an-int"


def test_parallel_helpers_cover_error_and_parallel_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover parallel execution fallbacks, scans, reads, and writes."""

    def maybe_fail(value: int) -> int:
        if value == 2:
            raise ValueError("boom")
        return value * 10

    mapped = parallel_mod.parallel_map(maybe_fail, [1, 2, 3], max_workers=2)
    assert mapped == {1: 10, 3: 30}

    monkeypatch.setattr(
        parallel_mod,
        "ProcessPoolExecutor",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no process pool")),
    )

    py_file = tmp_path / "a.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    parsed = parallel_mod.parallel_parse_files([str(py_file)], max_workers=1)
    assert str(py_file) in parsed
    assert parsed[str(py_file)] is not None

    root = tmp_path / "scan"
    (root / "one").mkdir(parents=True)
    (root / "two").mkdir()
    (root / "one" / "keep.py").write_text("print('one')\n", encoding="utf-8")
    (root / "two" / "keep.py").write_text("print('two')\n", encoding="utf-8")
    found = parallel_mod.parallel_scan_files(
        str(root), lambda path: path.endswith(".py"), max_workers=4
    )
    assert len(found) == 2

    assert parallel_mod.parallel_list_directories([], max_workers=8) == {}

    many_dirs = []
    for idx in range(5):
        directory = tmp_path / f"dir{idx}"
        directory.mkdir()
        (directory / f"file{idx}.txt").write_text(str(idx), encoding="utf-8")
        many_dirs.append(str(directory))
    listed = parallel_mod.parallel_list_directories(many_dirs, max_workers=4)
    assert len(listed) == 5
    assert all(isinstance(items, list) for items in listed.values())

    source = tmp_path / "src.txt"
    source.write_text("hello", encoding="utf-8")
    read = parallel_mod.parallel_read_files([str(source)], max_workers=2)
    assert read[str(source)] == "hello"

    target = tmp_path / "out.txt"
    written = parallel_mod.parallel_write_files({str(target): "world"}, max_workers=2)
    assert written[str(target)] is True
    assert target.read_text(encoding="utf-8") == "world"


def test_version_engine_version_and_git_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover version bumps, local reads, cache TTL, and git suggestions."""
    monkeypatch.setenv("PYFORGE_PYPI_CACHE_TTL", "bad")
    assert version_mod._get_pypi_cache_ttl() == 600

    assert version_mod.calculate_next_version("1.2.3", "alpha") == "1.2.4a1"
    assert version_mod.calculate_next_version("1.2.3", "beta") == "1.2.4b1"
    assert version_mod.calculate_next_version("1.2.3", "rc") == "1.2.4rc1"
    assert version_mod.calculate_next_version("1.2.3a1", "alpha") == "1.2.3a2"
    assert version_mod.calculate_next_version("1.2.3b1", "rc") == "1.2.3rc1"

    cache_path = tmp_path / "version.txt"
    cache_path.write_text('__version__ = "2.3.4"\n', encoding="utf-8")
    assert version_mod.read_local_version(str(cache_path)) == "2.3.4"
    cache_path.write_text("3.4.5\n", encoding="utf-8")
    assert version_mod.read_local_version(str(cache_path)) == "3.4.5"

    monkeypatch.setattr(version_mod, "get_project_details", lambda: ("demo", "dynamic"))
    monkeypatch.setattr(
        version_mod, "find_project_root", lambda _current: str(tmp_path)
    )
    monkeypatch.setattr(version_mod, "fetch_latest_version", lambda _name: None)
    cache_dir = tmp_path / ".pyforge-deploy-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "version_cache").write_text("1.0.0", encoding="utf-8")
    assert version_mod.get_dynamic_version() == "1.0.0"

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_major(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        log = textwrap.dedent(
            """
            feat!: major change

            ---COMMIT_SEP---
            """
        ).strip()
        return subprocess.CompletedProcess(cmd, 0, stdout=log, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_major)
    assert version_mod.suggest_bump_from_git() == "proud"

    def fake_run_fix(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        log = textwrap.dedent(
            """
            fix: bug fix

            ---COMMIT_SEP---
            """
        ).strip()
        return subprocess.CompletedProcess(cmd, 0, stdout=log, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_fix)
    assert version_mod.suggest_bump_from_git() == "shame"

    def fake_run_fail(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd, stderr="fail")

    monkeypatch.setattr(subprocess, "run", fake_run_fail)
    assert version_mod.suggest_bump_from_git() == "shame"


def test_docker_engine_helpers_and_entry_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover dependency parsing, caching, and entry-point detection branches."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"
            version = "0.1.0"
            dependencies = ["requests>=2.0", "attrs==23.1"]
            requires-python = ">=3.11"
            """
        ),
        encoding="utf-8",
    )
    assert docker_engine_mod._get_declared_dependencies(str(tmp_path)) == [
        "requests",
        "attrs",
    ]

    monkeypatch.setenv("PYFORGE_AST_CACHE_TTL", "bad")
    assert docker_engine_mod._get_ast_cache_ttl() == 300

    cache_payload = {
        "signature": "sig",
        "created_at": 0,
        "report": {"ok": True},
    }
    cache_file = tmp_path / ".pyforge-deploy-cache" / "ast_scan_cache.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps(cache_payload), encoding="utf-8")
    assert docker_engine_mod._load_cached_dependency_report(
        str(tmp_path), "sig", ttl_seconds=0
    ) == {"ok": True}
    assert (
        docker_engine_mod._load_cached_dependency_report(
            str(tmp_path), "mismatch", ttl_seconds=0
        )
        is None
    )

    monkeypatch.setenv("PYFORGE_HEAVY_HITTER_MB", "0")
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    big_dir = site_dir / "bigpkg"
    big_dir.mkdir()
    tiny_file = site_dir / "tiny.py"
    tiny_file.write_text("x = 1\n", encoding="utf-8")

    def fake_find_spec(name: str) -> SimpleNamespace | None:
        if name == "bigpkg":
            return SimpleNamespace(
                submodule_search_locations=[str(big_dir)],
                origin=None,
            )
        if name == "tiny":
            return SimpleNamespace(
                submodule_search_locations=None, origin=str(tiny_file)
            )
        return None

    monkeypatch.setattr(
        docker_engine_mod, "_get_site_package_dirs", lambda _path: [str(site_dir)]
    )
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(
        docker_engine_mod,
        "parallel_compute_sizes",
        lambda paths, max_workers=8: {
            path: (1024 if path.endswith("bigpkg") else 1) for path in paths
        },
    )
    heavy = docker_engine_mod._detect_heavy_hitters_by_size(
        str(tmp_path), ["bigpkg", "tiny"]
    )
    assert "bigpkg" in heavy

    src = tmp_path / "src"
    src.mkdir()
    tool = src / "tool.py"
    tool.write_text(
        "def run() -> None:\n    pass\n\nif __name__ == '__main__':\n    run()\n",
        encoding="utf-8",
    )
    assert docker_engine_mod.detect_entry_point(str(tmp_path)) == os.path.join(
        "src", "tool.py"
    )

    assert (
        docker_engine_mod._contains_main_guard("if __name__ == '__main__':\n    pass\n")
        is True
    )
    assert docker_engine_mod._is_ignored_for_entry_scan("tests/main.py") is True
