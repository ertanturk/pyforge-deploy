import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from pyforge_deploy.builders.docker import DockerBuilder


def test_build_wheelhouse_creates_wheels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: create temp project dir and fake requirements files
    monkeypatch.chdir(tmp_path)
    req: Path = tmp_path / "requirements-docker.txt"
    req.write_text("requests\n")
    wheels_dir: Path = tmp_path / "wheels"

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


def test_dockerfile_template_avoids_duplicate_local_copy() -> None:
    """Template should avoid copying /root/.local twice in non-root mode."""
    template_path = Path("src/pyforge_deploy/templates/Dockerfile.j2")
    content = template_path.read_text(encoding="utf-8")

    assert "FROM python:{{ python_image }} AS runtime" in content
    assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in content
    assert "PIP_NO_CACHE_DIR=1" in content
    assert "python -m pip install --upgrade pip wheel" in content

    legacy_duplicate_block = (
        "COPY --from=builder /root/.local /root/.local\n"
        'ENV PATH="/root/.local/bin:$PATH"\n\n'
        "{% if non_root %}"
    )
    assert legacy_duplicate_block not in content
