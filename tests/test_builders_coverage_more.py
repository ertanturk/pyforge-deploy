"""Additional coverage tests for Docker, PyPI, and version helpers."""

from __future__ import annotations

import builtins
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

import pyforge_deploy.builders.docker as docker_mod
import pyforge_deploy.builders.docker_engine as docker_engine_mod
import pyforge_deploy.builders.pypi as pypi_mod
import pyforge_deploy.builders.version_engine as version_mod
import pyforge_deploy.config as config_mod


def test_docker_requirements_wheelhouse_and_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover Docker requirements generation, wheelhouse, and confirmation paths."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    builders_dir = tmp_path / "builders"
    builders_dir.mkdir()
    monkeypatch.setattr(docker_mod, "__file__", str(builders_dir / "docker.py"))

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "Dockerfile.j2").write_text(
        'FROM python:{{ python_image }}\nCMD ["{{ entry_point }}"]\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        docker_mod,
        "detect_dependencies",
        lambda _path: {"final_list": ["requests"], "heavy_hitters": ["numpy"]},
    )
    monkeypatch.setattr(docker_mod, "get_python_version", lambda: "3.12")

    settings: dict[str, object] = {
        "docker_image": "demo/app:1.0.0",
        "docker_python": "3.12",
        "docker_wheelhouse": True,
        "docker_non_root": True,
    }

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        return settings.get(tool_key, default)

    monkeypatch.setattr(docker_mod, "resolve_setting", fake_resolve_setting)

    builder = docker_mod.DockerBuilder(
        entry_point="src/app/cli.py",
        image_tag="demo/app:1.0.0",
        verbose=True,
    )

    builder._generate_docker_requirements(["requests"], ["numpy"])
    assert builder.req_docker_path.exists()
    assert builder.heavy_req_path.exists()
    assert "requests" in builder.req_docker_path.read_text(encoding="utf-8")
    assert "numpy" in builder.heavy_req_path.read_text(encoding="utf-8")

    dockerignore = tmp_path / ".dockerignore"
    dockerignore.write_text(".git\n", encoding="utf-8")
    builder._ensure_dockerignore_sanity()
    assert "tests" in dockerignore.read_text(encoding="utf-8")

    build_calls: list[tuple[dict[str, Any], bool]] = []

    def fake_build_wheelhouse(report: dict[str, Any]) -> None:
        build_calls.append((report, True))

    monkeypatch.setattr(builder, "_build_wheelhouse", fake_build_wheelhouse)
    builder.render_template()
    assert build_calls
    assert (tmp_path / "Dockerfile").exists()

    builder.dry_run = True
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "n")
    with pytest.raises(SystemExit) as excinfo:
        builder._confirm("confirm?")
    assert excinfo.value.code == 0


def test_docker_build_image_and_push_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cover buildx, cleanup, and push paths."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(
        docker_mod, "__file__", str(tmp_path / "builders" / "docker.py")
    )
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "Dockerfile.j2").write_text(
        'FROM python:3.12\nCMD ["app.py"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(docker_mod, "detect_dependencies", lambda _path: {})
    monkeypatch.setattr(docker_mod, "get_python_version", lambda: "3.12")
    monkeypatch.setattr(
        docker_mod,
        "resolve_setting",
        lambda cli_value, tool_key, env_keys=None, default=None: (
            "demo/app:1.0.0"
            if tool_key == "docker_image"
            else (
                "linux/amd64,linux/arm64" if tool_key == "docker_platforms" else default
            )
        ),
    )

    builder = docker_mod.DockerBuilder(
        entry_point="app.py",
        image_tag="demo/app:1.0.0",
        platforms="linux/amd64,linux/arm64",
    )
    (builder.req_docker_path).write_text("requests\n", encoding="utf-8")
    (builder.heavy_req_path).write_text("numpy\n", encoding="utf-8")

    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[Any]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    builder.build_image(push=True)
    assert commands[0][0:3] == ["docker", "buildx", "build"]
    assert "--push" in commands[0]
    assert not builder.req_docker_path.exists()
    assert not builder.heavy_req_path.exists()

    builder.dry_run = True
    monkeypatch.setattr(
        docker_mod.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected run")),
    )
    builder.push_image()
    assert capsys.readouterr().out is not None


def test_pypi_oidc_collect_cleanup_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover OIDC minting, artifact collection, cleanup, and build commands."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(pypi_mod, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("PYPI_TOKEN", "fake-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://example.invalid/token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "request-token")

    class _Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload
            self.status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(req: object, timeout: float = 0) -> _Response:
        url = getattr(req, "full_url", req)
        if "mint-token" in str(url):
            return _Response({"token": "minted-token"})
        return _Response({"value": "github-jwt"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    dist = pypi_mod.PyPIDistributor(use_test_pypi=True, verbose=True)
    assert dist._get_oidc_token() == "minted-token"

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    wheel = dist_dir / "demo-1.0.0-py3-none-any.whl"
    sdist = dist_dir / "demo-1.0.0.tar.gz"
    wheel.write_text("wheel", encoding="utf-8")
    sdist.write_text("sdist", encoding="utf-8")
    assert dist._collect_dist_files("1.0.0", "wheel") == [wheel]
    assert dist._collect_dist_files("1.0.0", "both") == [wheel, sdist]

    (tmp_path / "build").mkdir()
    (tmp_path / "demo.egg-info").mkdir()
    dist._cleanup()
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "demo.egg-info").exists()

    monkeypatch.setattr(pypi_mod, "fetch_latest_version", lambda _name: "1.0.0")
    with pytest.raises(pypi_mod.PyPIDeployError):
        dist._pre_flight_check("demo", "1.0.0")

    monkeypatch.setattr(shutil, "which", lambda name: None)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[Any]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    dist._build_distributions("wheel")
    assert any(cmd[1:3] == ["-m", "build"] for cmd in calls)
    assert any("--wheel" in cmd for cmd in calls)


def test_version_engine_stale_cache_and_auto_increment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover stale PyPI cache fallback and cache-writing auto increment."""
    monkeypatch.setattr(version_mod, "get_project_path", lambda: str(tmp_path))
    cache_dir = tmp_path / ".pyforge-deploy-cache"

    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "pypi_network_cache.json"
    cache_file.write_text(
        json.dumps({"demo": {"version": "9.9.9", "fetched_at": 0}}),
        encoding="utf-8",
    )

    def fake_urlopen(_url: object, timeout: float = 3.0) -> Any:
        raise URLError("offline")

    monkeypatch.setattr(version_mod, "urlopen", fake_urlopen)
    version_mod._PYPI_CACHE.clear()
    assert version_mod.fetch_latest_version("demo", timeout=0.1) == "9.9.9"

    monkeypatch.setattr(version_mod, "get_project_details", lambda: ("demo", "dynamic"))
    monkeypatch.setattr(
        version_mod, "find_project_root", lambda _current: str(tmp_path)
    )
    monkeypatch.setattr(
        version_mod, "fetch_latest_version", lambda _name, timeout=3.0: None
    )
    (tmp_path / ".version_cache").write_text("1.0.0", encoding="utf-8")

    bumped = version_mod.get_dynamic_version(AUTO_INCREMENT=True, WRITE_CACHE=True)
    assert bumped == "1.0.1"
    assert (tmp_path / ".version_cache").read_text(encoding="utf-8") == "1.0.1"


def test_version_engine_cache_helpers_and_project_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover cache helpers, memory cache hits, and static project versions."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("invalid toml", encoding="utf-8")
    monkeypatch.setattr(version_mod, "get_pyproject_path", lambda: str(pyproject))
    assert version_mod.get_tool_config() == {}

    cache_dir = tmp_path / ".pyforge-deploy-cache"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "pypi_network_cache.json"
    now = 1_700_000_000
    cache_file.write_text(
        json.dumps({"demo": {"version": "1.2.3", "fetched_at": now}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(version_mod.time, "time", lambda: now + 1)
    assert version_mod._read_pypi_cached_version("demo", str(tmp_path)) == "1.2.3"
    assert version_mod._read_stale_pypi_cached_version("demo", str(tmp_path)) == "1.2.3"

    version_mod._write_pypi_cached_version("demo", "2.0.0", str(tmp_path))
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    assert payload["demo"]["version"] == "2.0.0"

    version_mod._PYPI_CACHE["cached-demo"] = "9.9.9"
    monkeypatch.setattr(version_mod, "get_project_path", lambda: str(tmp_path))
    assert version_mod.fetch_latest_version("cached-demo") == "9.9.9"

    monkeypatch.setattr(version_mod, "get_project_details", lambda: ("demo", "1.2.3"))
    assert version_mod.get_dynamic_version() == "1.2.3"


def test_config_getters_defaults_and_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover default paths and string parsing in config helpers."""
    monkeypatch.setattr(config_mod, "get_tool_config", lambda: {})
    assert config_mod.get_bool_setting("false", "unused", default=True) is False
    assert config_mod.get_bool_setting("1", "unused", default=False) is True
    assert config_mod.get_int_setting("bad", "unused", default=9) == 9
    assert config_mod.get_list_setting(None, "unused", default=["x"]) == ["x"]
    assert config_mod.get_list_setting("a, b, ,c", "unused", default=None) == [
        "a",
        "b",
        "c",
    ]


def test_docker_engine_dependency_fallback_and_cache_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover dependency parsing fallback paths and cache error handling."""
    (tmp_path / "requirements.txt").write_text(
        "requests>=2.0\n# comment\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text("invalid toml", encoding="utf-8")
    assert docker_engine_mod._get_declared_dependencies(str(tmp_path)) == ["requests"]

    monkeypatch.setattr(
        docker_engine_mod,
        "_load_ast_cache",
        lambda _project_path: {"signature": "sig", "created_at": 0, "report": {"x": 1}},
    )
    assert docker_engine_mod._load_cached_dependency_report(
        str(tmp_path), "sig", 0
    ) == {"x": 1}

    monkeypatch.setattr(
        docker_engine_mod, "parallel_compute_sizes", lambda paths, max_workers=8: {}
    )
    assert docker_engine_mod._detect_heavy_hitters_by_size(str(tmp_path), []) == []


def test_docker_error_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover Docker requirement, wheelhouse, and push error branches."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    builders_dir = tmp_path / "builders"
    builders_dir.mkdir()
    monkeypatch.setattr(docker_mod, "__file__", str(builders_dir / "docker.py"))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "Dockerfile.j2").write_text(
        'FROM python:3.12\nCMD ["app.py"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(docker_mod, "detect_dependencies", lambda _path: {})
    monkeypatch.setattr(docker_mod, "get_python_version", lambda: "3.12")
    monkeypatch.setattr(
        docker_mod,
        "resolve_setting",
        lambda cli_value, tool_key, env_keys=None, default=None: (
            "demo/app:1.0.0" if tool_key == "docker_image" else default
        ),
    )

    builder = docker_mod.DockerBuilder(entry_point="app.py", image_tag="demo/app:1.0.0")
    builder.dry_run = True
    builder._generate_docker_requirements([], [])

    builder.dry_run = False
    monkeypatch.setattr(
        "builtins.open",
        lambda *a, **k: (_ for _ in ()).throw(OSError("fail")),
    )
    with pytest.raises(docker_mod.DockerBuildError):
        builder._generate_docker_requirements(["requests"], ["numpy"])

    monkeypatch.setattr(
        docker_mod.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, a[0])),
    )
    builder.req_docker_path.write_text("requests\n", encoding="utf-8")
    builder.heavy_req_path.write_text("numpy\n", encoding="utf-8")
    with pytest.raises(docker_mod.DockerBuildError):
        builder._build_wheelhouse({})

    builder.dry_run = True
    builder.push_image()


def test_pypi_helper_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover PyPI helper branches for missing tokens and build failures."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(pypi_mod, "load_dotenv", lambda **kwargs: None)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    dist = pypi_mod.PyPIDistributor(verbose=True)
    assert dist._get_oidc_token() is None
    assert dist._collect_dist_files("1.0.0", "both") == []

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, a[0])),
    )
    with pytest.raises(pypi_mod.PyPIDeployError):
        dist._build_distributions("wheel")
