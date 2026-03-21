import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

import pyforge_deploy.builders.docker as docker_mod
from pyforge_deploy.builders.docker import DockerBuilder


def test_build_wheelhouse_creates_wheels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: create temp project dir and fake requirements files
    monkeypatch.chdir(tmp_path)
    req: Path = tmp_path / "requirements-docker.txt"
    req.write_text("requests\n")
    wheels_dir: Path = tmp_path / ".pyforge-deploy-cache" / "wheels"

    calls: dict[str, int] = {"count": 0}

    def fake_run(
        cmd: Any, check: bool = True, cwd: str | None = None, **kwargs: Any
    ) -> Any:
        # create a dummy wheel file to simulate pip wheel
        calls["count"] += 1
        wheels_dir.mkdir(exist_ok=True)
        wheel: Path = wheels_dir / f"dummy-{calls['count']}.whl"
        wheel.write_text("binary")

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    db = DockerBuilder(dry_run=False)
    # run wheelhouse builder
    report: dict[str, Any] = {"final_list": ["requests"], "heavy_hitters": []}
    cast(Any, db)._build_wheelhouse(report)

    assert wheels_dir.exists()
    assert any(wheels_dir.iterdir())


def test_build_wheelhouse_includes_transitive_dependencies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Wheelhouse build must not use --no-deps to support offline install."""
    monkeypatch.chdir(tmp_path)
    req: Path = tmp_path / "requirements-docker.txt"
    req.write_text("build\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(
        cmd: Any, check: bool = True, cwd: str | None = None, **kwargs: Any
    ) -> Any:
        commands.append(list(cmd))

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    db = DockerBuilder(dry_run=False)
    cast(Any, db)._build_wheelhouse({"final_list": ["build"], "heavy_hitters": []})

    assert commands, "Expected at least one pip wheel command"
    assert all("--no-deps" not in cmd for cmd in commands)


def test_build_wheelhouse_prefers_uv_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Wheelhouse build should prefer uv for faster dependency resolution."""
    monkeypatch.chdir(tmp_path)
    req: Path = tmp_path / "requirements-docker.txt"
    req.write_text("requests\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(
        cmd: Any, check: bool = True, cwd: str | None = None, **kwargs: Any
    ) -> Any:
        commands.append(list(cmd))

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    db = DockerBuilder(dry_run=False)
    monkeypatch.setattr(db, "_uv_supports_pip_wheel", lambda _uv: True)
    cast(Any, db)._build_wheelhouse({"final_list": ["requests"], "heavy_hitters": []})

    assert commands, "Expected uv wheel command"
    assert commands[0][0] == "/usr/bin/uv"
    assert commands[0][1:3] == ["pip", "wheel"]


def test_build_wheelhouse_falls_back_to_pip_when_uv_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Wheelhouse build should retry with pip when uv command fails."""
    monkeypatch.chdir(tmp_path)
    req: Path = tmp_path / "requirements-docker.txt"
    req.write_text("requests\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(
        cmd: Any, check: bool = True, cwd: str | None = None, **kwargs: Any
    ) -> Any:
        as_list = list(cmd)
        commands.append(as_list)
        if as_list[0] == "/usr/bin/uv":
            raise subprocess.CalledProcessError(1, as_list)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    db = DockerBuilder(dry_run=False)
    monkeypatch.setattr(db, "_uv_supports_pip_wheel", lambda _uv: True)
    cast(Any, db)._build_wheelhouse({"final_list": ["requests"], "heavy_hitters": []})

    assert len(commands) >= 2
    assert commands[0][0] == "/usr/bin/uv"
    assert Path(commands[1][0]).name.startswith("python")
    assert commands[1][1:4] == ["-m", "pip", "wheel"]


def test_build_wheelhouse_uses_pip_when_uv_wheel_is_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Should skip uv wheel path when uv pip wheel subcommand is not supported."""
    monkeypatch.chdir(tmp_path)
    req: Path = tmp_path / "requirements-docker.txt"
    req.write_text("requests\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(
        cmd: Any, check: bool = True, cwd: str | None = None, **kwargs: Any
    ) -> Any:
        commands.append(list(cmd))

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    db = DockerBuilder(dry_run=False)
    monkeypatch.setattr(db, "_uv_supports_pip_wheel", lambda _uv: False)
    cast(Any, db)._build_wheelhouse({"final_list": ["requests"], "heavy_hitters": []})

    assert commands, "Expected a pip wheel command when uv wheel is unsupported"
    assert all(cmd[0] != "/usr/bin/uv" for cmd in commands)
    assert commands[0][1:4] == ["-m", "pip", "wheel"]


def test_dockerfile_template_avoids_duplicate_local_copy() -> None:
    """Template should avoid copying /root/.local twice in non-root mode."""
    template_path = Path("src/pyforge_deploy/templates/Dockerfile.j2")
    content = template_path.read_text(encoding="utf-8")

    assert "FROM python:{{ python_image }} AS runtime" in content
    assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in content
    assert "PIP_NO_CACHE_DIR=1" in content
    assert "python -m pip install --upgrade pip setuptools wheel" in content
    assert "COPY .pyforge-deploy-cache/wheels /wheels" in content
    assert "--no-build-isolation" in content
    assert "--find-links /wheels -r requirements-docker.txt &&" in content
    assert (
        "COPY --from=builder /app/requirements-docker.txt /tmp/requirements-docker.txt"
        in content
    )
    assert (
        "python -m pip install --no-cache-dir --no-index --find-links /wheels "
        "-r /tmp/requirements-docker.txt" in content
    )
    assert "python -m pip install --user --no-cache-dir --no-deps ." not in content

    legacy_duplicate_block = (
        "COPY --from=builder /root/.local /root/.local\n"
        'ENV PATH="/root/.local/bin:$PATH"\n\n'
        "{% if non_root %}"
    )
    assert legacy_duplicate_block not in content


def test_render_template_parses_string_boolean_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """String flags like '0'/'false' should disable wheelhouse and non-root."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    builders_dir = tmp_path / "builders"
    builders_dir.mkdir()
    monkeypatch.setattr(docker_mod, "__file__", str(builders_dir / "docker.py"))

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "Dockerfile.j2").write_text(
        "WHEELHOUSE={{ use_wheelhouse }}\nNON_ROOT={{ non_root }}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(docker_mod, "get_python_version", lambda: "3.12")
    monkeypatch.setattr(
        docker_mod,
        "detect_dependencies",
        lambda _path: {"final_list": [], "heavy_hitters": [], "has_pyproject": False},
    )

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "docker_wheelhouse":
            return "0"
        if tool_key == "docker_non_root":
            return "false"
        if tool_key == "docker_image":
            return "demo/app:1.0.0"
        return default

    monkeypatch.setattr(docker_mod, "resolve_setting", fake_resolve_setting)

    builder = DockerBuilder(entry_point="app.py", image_tag="demo/app:1.0.0")

    called = {"wheelhouse": False}

    def fake_build_wheelhouse(report: dict[str, Any]) -> None:
        called["wheelhouse"] = True

    monkeypatch.setattr(builder, "_build_wheelhouse", fake_build_wheelhouse)

    builder.render_template()

    assert called["wheelhouse"] is False
    output = (tmp_path / "Dockerfile").read_text(encoding="utf-8")
    assert "WHEELHOUSE=False" in output
    assert "NON_ROOT=False" in output


def test_render_template_disables_wheelhouse_for_multi_platform_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Wheelhouse must be disabled for multi-platform builds to avoid arch mismatch."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    builders_dir = tmp_path / "builders"
    builders_dir.mkdir()
    monkeypatch.setattr(docker_mod, "__file__", str(builders_dir / "docker.py"))

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "Dockerfile.j2").write_text(
        "WHEELHOUSE={{ use_wheelhouse }}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(docker_mod, "get_python_version", lambda: "3.12")
    monkeypatch.setattr(
        docker_mod,
        "detect_dependencies",
        lambda _path: {"final_list": [], "heavy_hitters": [], "has_pyproject": False},
    )

    def fake_resolve_setting(
        cli_value: object,
        tool_key: str,
        env_keys: tuple[str, ...] | None = None,
        default: object = None,
    ) -> object:
        if tool_key == "docker_wheelhouse":
            return True
        if tool_key == "docker_image":
            return "demo/app:1.0.0"
        return default

    monkeypatch.setattr(docker_mod, "resolve_setting", fake_resolve_setting)

    builder = DockerBuilder(entry_point="app.py", image_tag="demo/app:1.0.0")
    builder.platforms = "linux/amd64,linux/arm64"

    called = {"wheelhouse": False}

    def fake_build_wheelhouse(report: dict[str, Any]) -> None:
        called["wheelhouse"] = True

    monkeypatch.setattr(builder, "_build_wheelhouse", fake_build_wheelhouse)

    builder.render_template()

    assert called["wheelhouse"] is False
    output = (tmp_path / "Dockerfile").read_text(encoding="utf-8")
    assert "WHEELHOUSE=False" in output
