"""Parallel execution utilities for pyforge_deploy.

Provides threading and concurrent execution helpers for CPU/IO-bound tasks,
including file operations, AST parsing, size calculations, and subprocess execution.
"""

import ast
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Literal

from pyforge_deploy.logutil import log as logutil


def _log(message: str, color: str = "blue") -> None:
    """Log message if in verbose/CI mode."""
    verbose = os.environ.get("PYFORGE_VERBOSE") == "1" or os.environ.get("CI") == "true"
    if verbose:
        logutil(message, level="debug", color=color, component="parallel")


def _parse_python_file(path: str) -> tuple[str, ast.AST | None]:
    """Parse a single Python file and return (path, AST-or-None)."""
    try:
        with open(path, "rb") as f:
            content = f.read()
            if not content.strip():
                return path, None
            return path, ast.parse(content, filename=path)
    except (SyntaxError, OSError):
        return path, None


def parallel_map[T, U](
    func: Callable[[T], U], items: list[T], max_workers: int = 8
) -> dict[T, U]:
    """Execute function on items in parallel.

    Args:
        func: Function to apply to each item.
        items: List of items to process.
        max_workers: Maximum number of concurrent threads.

    Returns:
        Dictionary mapping items to their results.
    """
    results: dict[T, U] = {}
    if not items:
        return results

    _log(
        f"Processing {len(items)} items in parallel (max_workers={max_workers})", "cyan"
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(func, item): item for item in items}
        completed = 0
        for future in as_completed(futures):
            item = futures[future]
            try:
                results[item] = future.result()
                completed += 1
                if completed % max(1, len(items) // 10) == 0:
                    _log(f"Progress: {completed}/{len(items)} items completed", "blue")
            except Exception as e:
                _log(f"Error processing {item}: {e}", "yellow")
                continue

    _log(f"Completed {len(results)} items", "green")
    return results


def parallel_parse_files(
    file_paths: list[str], max_workers: int = 8
) -> dict[str, ast.AST | None]:
    """Parse multiple Python files in parallel.

    Args:
        file_paths: List of absolute paths to .py files.
        max_workers: Maximum number of concurrent parsing threads.

    Returns:
        Dictionary mapping file paths to parsed AST (None if parse failed).
    """

    if not file_paths:
        return {}

    worker_count = max_workers if max_workers > 0 else get_optimal_workers("cpu")
    _log(
        (
            f"Parsing {len(file_paths)} Python files in process pool "
            f"(max_workers={worker_count})"
        ),
        "cyan",
    )

    # AST parsing is CPU-bound; prefer process-based parallelism.
    # Fallback to thread-based mapping in restricted environments.
    results: dict[str, ast.AST | None] = {}
    try:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_parse_python_file, path): path for path in file_paths
            }
            for future in as_completed(futures):
                path = futures[future]
                try:
                    parsed_path, tree = future.result()
                    results[parsed_path] = tree
                except Exception as e:
                    _log(f"Process parse failed for {path}: {e}", "yellow")
                    results[path] = None
        return results
    except Exception as e:
        _log(f"Process parsing unavailable, falling back to threads: {e}", "yellow")

        def parse_tree(path: str) -> ast.AST | None:
            return _parse_python_file(path)[1]

        return parallel_map(
            parse_tree, file_paths, max_workers=get_optimal_workers("io")
        )


def parallel_compute_sizes(paths: list[str], max_workers: int = 8) -> dict[str, int]:
    """Compute directory/file sizes in parallel.

    Args:
        paths: List of absolute paths to files or directories.
        max_workers: Maximum number of concurrent threads.

    Returns:
        Dictionary mapping paths to total size in bytes.
    """

    def compute_size(path: str) -> int:
        """Calculate total size of file or directory."""
        total = 0
        if os.path.isfile(path):
            try:
                return os.path.getsize(path)
            except Exception:
                return 0
        try:
            for root, _, files in os.walk(path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total += os.path.getsize(fp)
                    except (OSError, PermissionError):
                        continue
        except OSError:
            pass
        return total

    _log(f"Computing sizes for {len(paths)} paths in parallel", "cyan")
    return parallel_map(compute_size, paths, max_workers=max_workers)


def parallel_scan_files(
    root_path: str,
    pattern_check: Callable[[str], bool],
    max_workers: int = 8,
) -> list[str]:
    """Scan directory tree for files matching a pattern in parallel.

    Args:
        root_path: Root directory to scan.
        pattern_check: Function that returns True for matching files.
        max_workers: Maximum number of concurrent threads.

    Returns:
        List of all matching file paths.
    """
    ignore_dirs = {
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".git",
        "build",
        "dist",
        ".pytest_cache",
        ".tox",
        "node_modules",
    }

    def scan_subtree(start_path: str) -> list[str]:
        matched: list[str] = []
        for root, dirs, files in os.walk(start_path):
            dirs[:] = [
                d for d in dirs if d not in ignore_dirs and not d.startswith(".")
            ]
            for file in files:
                file_path = os.path.join(root, file)
                if pattern_check(file_path):
                    matched.append(file_path)
        return matched

    if not os.path.isdir(root_path):
        return []

    root_files: list[str] = []
    child_dirs: list[str] = []

    with os.scandir(root_path) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in ignore_dirs and not entry.name.startswith("."):
                    child_dirs.append(entry.path)
            elif entry.is_file(follow_symlinks=False):
                if pattern_check(entry.path):
                    root_files.append(entry.path)

    # For small trees, sequential scan is typically faster than pool overhead.
    if len(child_dirs) <= 1 or max_workers <= 1:
        all_files = list(root_files)
        for child_dir in child_dirs:
            all_files.extend(scan_subtree(child_dir))
        _log(f"Found {len(all_files)} matching files", "green")
        return all_files

    worker_count = min(max_workers, len(child_dirs))
    _log(
        (
            f"Scanning {len(child_dirs)} subtrees in parallel "
            f"(max_workers={worker_count})"
        ),
        "cyan",
    )

    all_files = list(root_files)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(scan_subtree, d): d for d in child_dirs}
        for future in as_completed(futures):
            try:
                all_files.extend(future.result())
            except Exception as e:
                _log(f"Subtree scan failed: {e}", "yellow")

    _log(f"Found {len(all_files)} matching files", "green")
    return all_files


def parallel_extract_from_files[T](
    file_paths: list[str],
    extractor: Callable[[str], T],
    max_workers: int = 8,
) -> dict[str, T]:
    """Extract data from multiple files in parallel.

    Args:
        file_paths: List of file paths.
        extractor: Function to extract data from a file path.
        max_workers: Maximum number of concurrent threads.

    Returns:
        Dictionary mapping file paths to extracted data.
    """
    _log(f"Extracting data from {len(file_paths)} files in parallel", "cyan")
    return parallel_map(extractor, file_paths, max_workers=max_workers)


def parallel_list_directories(
    directory_paths: list[str], max_workers: int = 8
) -> dict[str, list[str]]:
    """List contents of multiple directories in parallel.

    Args:
        directory_paths: List of directory paths.
        max_workers: Maximum number of concurrent threads.

    Returns:
        Dictionary mapping directory paths to lists of items.
    """

    def list_dir(path: str) -> list[str]:
        """List directory contents safely."""
        try:
            return os.listdir(path) if os.path.isdir(path) else []
        except OSError:
            return []

    # For tiny inputs, thread setup overhead can outweigh benefits.
    if len(directory_paths) <= 4 or max_workers <= 1:
        return {path: list_dir(path) for path in directory_paths}

    _log(f"Listing {len(directory_paths)} directories in parallel", "cyan")
    return parallel_map(list_dir, directory_paths, max_workers=max_workers)


def batch_execute_functions[T](
    functions: list[tuple[Callable[..., T], tuple[Any, ...], dict[str, Any]]],
    max_workers: int = 4,
) -> dict[int, T]:
    """Execute multiple functions in parallel.

    Args:
        functions: List of (callable, args, kwargs) tuples.
        max_workers: Maximum number of concurrent threads.

    Returns:
        Dictionary mapping function index to result.
    """

    def execute_func(
        idx: int, func: Callable[..., T], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[int, T]:
        """Execute a single function."""
        return idx, func(*args, **kwargs)

    _log(f"Executing {len(functions)} functions in parallel", "cyan")

    results: dict[int, T] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(execute_func, i, func, args, kwargs): i
            for i, (func, args, kwargs) in enumerate(functions)
        }

        for future in as_completed(futures):
            try:
                idx, result = future.result()
                results[idx] = result
            except Exception as e:
                _log(f"Error in parallel function execution: {e}", "yellow")

    return results


def get_optimal_workers(workload: Literal["io", "cpu"] = "io") -> int:
    """Calculate optimal worker count for I/O or CPU workloads.

    Args:
        workload: Workload type; use "io" for thread pools, "cpu" for process pools.

    Returns:
        Recommended number of workers based on CPU count and workload type.
    """
    cpu_count = os.cpu_count() or 1
    if workload == "cpu":
        # Avoid excessive context switching in process pools.
        return max(1, cpu_count)
    # I/O-bound workloads benefit from higher concurrency.
    return min(32, max(4, cpu_count * 2))


def parallel_read_files(
    file_paths: list[str], max_workers: int = 8
) -> dict[str, str | None]:
    """Read multiple files in parallel.

    Args:
        file_paths: List of absolute file paths.
        max_workers: Maximum number of concurrent threads.

    Returns:
        Dictionary mapping file paths to contents (None if read failed).
    """

    def read_file(path: str) -> str | None:
        """Read single file content."""
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    _log(f"Reading {len(file_paths)} files in parallel", "cyan")
    return parallel_map(read_file, file_paths, max_workers=max_workers)


def parallel_write_files(
    files: dict[str, str], max_workers: int = 8
) -> dict[str, bool]:
    """Write multiple files in parallel.

    Args:
        files: Dictionary mapping file paths to contents.
        max_workers: Maximum number of concurrent threads.

    Returns:
        Dictionary mapping file paths to success status.
    """

    def write_file(item: tuple[str, str]) -> tuple[str, bool]:
        """Write single file."""
        path, content = item
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return path, True
        except Exception:
            return path, False

    _log(f"Writing {len(files)} files in parallel", "cyan")
    results = parallel_map(write_file, list(files.items()), max_workers=max_workers)
    return {path: success for path, success in results.values()}
