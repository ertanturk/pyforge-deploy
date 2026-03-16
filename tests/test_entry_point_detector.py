"""Tests for entry point detection and zero-configuration usability."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from pyforge_deploy.builders.entry_point_detector import (
    detect_cli_modules,
    detect_entry_point,
    extract_entry_points_from_pyproject,
    find_main_blocks,
    list_potential_entry_points,
)


@pytest.fixture
def temp_project() -> Generator[Path, None, None]:
    """Create a temporary project directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_detect_entry_point_from_pyproject(temp_project: Path) -> None:
    """Test entry point detection from pyproject.toml."""
    pyproject = temp_project / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "test-app"

[project.scripts]
cli = "myapp.cli:main"
"""
    )
    result = detect_entry_point(str(temp_project))
    # Should extract module part before colon
    assert result is not None and "myapp" in result


def test_detect_entry_point_with_main_block(temp_project: Path) -> None:
    """Test detection of modules with __main__ blocks."""
    src = temp_project / "src" / "myapp"
    src.mkdir(parents=True)

    cli_file = src / "cli.py"
    cli_file.write_text(
        """
def run():
    pass

if __name__ == "__main__":
    run()
"""
    )

    result = detect_entry_point(str(temp_project))
    assert result is not None and "cli.py" in result


def test_detect_entry_point_with_main_module(temp_project: Path) -> None:
    """Test detection of common CLI module names."""
    src = temp_project / "src" / "myapp"
    src.mkdir(parents=True)

    # Create a main.py file
    main_file = src / "main.py"
    main_file.write_text("def main(): pass")

    result = detect_entry_point(str(temp_project))
    assert result is not None and "main.py" in result


def test_detect_entry_point_no_cli(temp_project: Path) -> None:
    """Test detection returns None for non-CLI projects."""
    src = temp_project / "src" / "libapp"
    src.mkdir(parents=True)

    # Create only a library module
    lib_file = src / "utils.py"
    lib_file.write_text("def helper(): pass")

    result = detect_entry_point(str(temp_project))
    assert result is None


def test_extract_entry_points_from_pyproject(temp_project: Path) -> None:
    """Test extraction of scripts from pyproject.toml."""
    pyproject = temp_project / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "test-app"

[project.scripts]
cli = "myapp.cli:main"
worker = "myapp.worker:start"
"""
    )

    result = extract_entry_points_from_pyproject(str(temp_project))
    assert "cli" in result
    assert "worker" in result
    assert result["cli"] == "myapp.cli:main"


def test_find_main_blocks(temp_project: Path) -> None:
    """Test finding Python files with __main__ blocks."""
    src = temp_project / "src" / "myapp"
    src.mkdir(parents=True)

    # File with main block
    with_main = src / "cli.py"
    with_main.write_text('if __name__ == "__main__": pass')

    # File without main block
    without_main = src / "utils.py"
    without_main.write_text("def helper(): pass")

    result = find_main_blocks(src)
    assert len(result) > 0
    assert any("cli" in m for m in result)


def test_detect_cli_modules(temp_project: Path) -> None:
    """Test detection of common CLI module names."""
    src = temp_project / "src" / "myapp"
    src.mkdir(parents=True)

    # Create CLI-named modules
    (src / "cli.py").write_text("")
    (src / "main.py").write_text("")
    (src / "app.py").write_text("")

    result = detect_cli_modules(src)
    assert len(result) >= 3
    assert any("cli" in m for m in result)
    assert any("main" in m for m in result)
    assert any("app" in m for m in result)


def test_list_potential_entry_points(temp_project: Path) -> None:
    """Test listing all potential entry points."""
    src = temp_project / "src" / "myapp"
    src.mkdir(parents=True)

    # Create multiple CLI modules
    (src / "cli.py").write_text('if __name__ == "__main__": pass')
    (src / "main.py").write_text("")
    (src / "app.py").write_text("")

    result = list_potential_entry_points(str(temp_project))
    assert len(result) > 0
    # Results should be deduplicated and sorted
    assert len(result) == len(set(result))


def test_zero_config_docker_build_no_args(temp_project: Path) -> None:
    """Test that docker-build works with zero arguments (requires auto-detection)."""
    # Setup minimal project
    src = temp_project / "src" / "testapp"
    src.mkdir(parents=True)

    (src / "cli.py").write_text('if __name__ == "__main__": print("hello")')

    pyproject = temp_project / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "test-app"
version = "0.1.0"
"""
    )

    # Should auto-detect entry point
    result = detect_entry_point(str(temp_project))
    assert result is not None
    assert "cli.py" in result
