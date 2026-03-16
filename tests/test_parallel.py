"""Tests for parallel execution utilities."""

from pathlib import Path

from pyforge_deploy.builders.parallel import (
    get_optimal_workers,
    parallel_list_directories,
    parallel_parse_files,
    parallel_scan_files,
)


def test_parallel_scan_files_respects_ignore_dirs(tmp_path: Path) -> None:
    """parallel_scan_files should skip ignored directories like .venv."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    kept = src_dir / "kept.py"
    kept.write_text("print('ok')", encoding="utf-8")

    ignored_dir = tmp_path / ".venv"
    ignored_dir.mkdir()
    ignored_file = ignored_dir / "ignored.py"
    ignored_file.write_text("print('ignore')", encoding="utf-8")

    found = parallel_scan_files(
        str(tmp_path), lambda p: p.endswith(".py"), max_workers=4
    )

    assert str(kept) in found
    assert str(ignored_file) not in found


def test_parallel_parse_files_handles_valid_and_invalid(tmp_path: Path) -> None:
    """parallel_parse_files should parse valid files and tolerate syntax errors."""
    valid_file = tmp_path / "valid.py"
    invalid_file = tmp_path / "invalid.py"

    valid_file.write_text("x = 1\n", encoding="utf-8")
    invalid_file.write_text("def broken(:\n", encoding="utf-8")

    parsed = parallel_parse_files([str(valid_file), str(invalid_file)], max_workers=2)

    assert parsed[str(valid_file)] is not None
    assert parsed[str(invalid_file)] is None


def test_parallel_list_directories_returns_contents(tmp_path: Path) -> None:
    """parallel_list_directories should return per-directory listings."""
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()

    (d1 / "a.txt").write_text("a", encoding="utf-8")
    (d2 / "b.txt").write_text("b", encoding="utf-8")

    listed = parallel_list_directories([str(d1), str(d2)], max_workers=8)

    assert "a.txt" in listed[str(d1)]
    assert "b.txt" in listed[str(d2)]


def test_get_optimal_workers_cpu_vs_io() -> None:
    """I/O worker recommendation should be >= CPU recommendation."""
    io_workers = get_optimal_workers("io")
    cpu_workers = get_optimal_workers("cpu")

    assert io_workers >= cpu_workers
    assert cpu_workers >= 1
