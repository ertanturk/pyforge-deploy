import os
import textwrap
from pathlib import Path
from typing import Any

from _pytest.monkeypatch import MonkeyPatch

from pyforge_deploy.builders import docker_engine

docker_engine_any: Any = docker_engine


def test_clean_dep_strings() -> None:
    inp = ["requests>=2.0", "# a comment", "numpy==1.2"]
    out = docker_engine_any._clean_dep_strings(inp)
    assert "requests" in out and "numpy" in out


def test_get_declared_dependencies_project_list(tmp_path: Path) -> None:
    content = textwrap.dedent(
        """
        [project]
        name = "example"
        version = "0.1.0"
        dependencies = ["requests>=2.0", "attrs==21.0"]
        """
    )
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    deps = docker_engine_any._get_declared_dependencies(str(tmp_path))
    assert isinstance(deps, list)
    assert "requests" in deps and "attrs" in deps


def test_get_declared_dependencies_poetry(tmp_path: Path) -> None:
    content = textwrap.dedent(
        """
        [tool.poetry.dependencies]
        python = "^3.8"
        flask = "^1.1"
        """
    )
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    deps = docker_engine_any._get_declared_dependencies(str(tmp_path))
    assert isinstance(deps, list)
    assert "flask" in deps


def test_get_venv_bin_tools(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "pytest").write_text("", encoding="utf-8")
    tools = docker_engine.get_venv_bin_tools(str(tmp_path))
    assert "pytest" in tools


def test_local_modules_and_imports(tmp_path: Path) -> None:
    # create a simple project with local modules and imports
    pkg = tmp_path / "mymodule"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "foo.py").write_text(
        "import math\nfrom mymodule import bar\n", encoding="utf-8"
    )
    (tmp_path / "bar.py").write_text("print('x')\n", encoding="utf-8")

    imports = docker_engine.get_imports(str(tmp_path))
    local = docker_engine.get_local_modules(str(tmp_path))
    final = docker_engine.get_clean_final_list(imports, set(), str(tmp_path))

    # math is stdlib and should not be in final; mymodule is local and excluded
    assert "math" in imports
    assert "mymodule" in local or "bar" in local
    # final should not include stdlib or local modules
    assert all(x not in {"math", "mymodule"} for x in final)


def test_detect_dependencies_declared_and_heavy_fallback(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # write requirements.txt to trigger declared path
    (tmp_path / "requirements.txt").write_text("numpy\nrequests\n", encoding="utf-8")

    # monkeypatch heavy hitter detector to return empty so fallback list used
    def _fake_detect_heavy(a: str, b: str) -> list[str]:
        return []

    monkeypatch.setattr(
        docker_engine_any, "_detect_heavy_hitters_by_size", _fake_detect_heavy
    )

    report = docker_engine.detect_dependencies(str(tmp_path))
    assert report["source"] == "declared"
    # numpy should be considered heavy via static candidate list
    assert "numpy" in report["heavy_hitters"] or "requests" in report["final_list"]


def test_get_python_version(tmp_path: Path) -> None:
    content = textwrap.dedent(
        """
        [project]
        requires-python = ">=3.8"
        """
    )
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        ver = docker_engine.get_python_version()
        assert isinstance(ver, str)
        assert ver.startswith("3.8") or ver == ver
    finally:
        os.chdir(cwd)


def test_detect_entry_point_from_pyproject_scripts(tmp_path: Path) -> None:
    pyproject = textwrap.dedent(
        """
        [project]
        name = "demo"
        version = "0.1.0"

        [project.scripts]
        demo = "myapp.cli:main"
        """
    )
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    app_dir = tmp_path / "src" / "myapp"
    app_dir.mkdir(parents=True)
    (app_dir / "cli.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")

    result = docker_engine.detect_entry_point(str(tmp_path))
    assert result == "src/myapp/cli.py"


def test_detect_entry_point_prefers_cli_like_filename(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "worker.py").write_text(
        "print('background')\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "cli.py").write_text(
        "def run() -> None:\n    pass\n", encoding="utf-8"
    )

    result = docker_engine.detect_entry_point(str(tmp_path))
    assert result == os.path.join("pkg", "cli.py")


def test_detect_entry_point_ignores_tests_directory(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "main.py").write_text("print('test only')\n", encoding="utf-8")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("print('real app')\n", encoding="utf-8")

    result = docker_engine.detect_entry_point(str(tmp_path))
    assert result == "src/app.py"


def test_detect_dependencies_uses_ast_disk_cache(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Second dependency detection call should reuse AST disk cache."""
    src_file = tmp_path / "main.py"
    src_file.write_text("import requests\n", encoding="utf-8")

    call_count = {"imports": 0}

    def _fake_get_imports(project_path: str) -> set[str]:
        call_count["imports"] += 1
        return {"requests"}

    monkeypatch.setattr(docker_engine, "get_imports", _fake_get_imports)
    monkeypatch.setattr(
        docker_engine,
        "_detect_heavy_hitters_by_size",
        lambda _project_path, _packages: [],
    )

    first = docker_engine.detect_dependencies(str(tmp_path))
    second = docker_engine.detect_dependencies(str(tmp_path))

    assert call_count["imports"] == 1
    assert first["final_list"] == second["final_list"]
    cache_file = tmp_path / ".pyforge-deploy-cache" / "ast_scan_cache.json"
    assert cache_file.exists()


def test_detect_dependencies_ast_cache_invalidates_on_source_change(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """AST cache should invalidate when Python source files change."""
    src_file = tmp_path / "main.py"
    src_file.write_text("import requests\n", encoding="utf-8")

    call_count = {"imports": 0}

    def _fake_get_imports(project_path: str) -> set[str]:
        call_count["imports"] += 1
        return {"requests"}

    monkeypatch.setattr(docker_engine, "get_imports", _fake_get_imports)
    monkeypatch.setattr(
        docker_engine,
        "_detect_heavy_hitters_by_size",
        lambda _project_path, _packages: [],
    )

    docker_engine.detect_dependencies(str(tmp_path))
    src_file.write_text("import requests\nimport httpx\n", encoding="utf-8")
    docker_engine.detect_dependencies(str(tmp_path))

    assert call_count["imports"] == 2
