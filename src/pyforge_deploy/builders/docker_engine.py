import ast
import json
import os
import re
import sys
import time
from hashlib import sha256
from typing import Any, cast

import toml

from pyforge_deploy.builders.parallel import (
    parallel_compute_sizes,
    parallel_parse_files,
    parallel_read_files,
    parallel_scan_files,
)
from pyforge_deploy.logutil import log as logutil

_CACHE_DIR_NAME = ".pyforge-deploy-cache"
_AST_CACHE_FILE_NAME = "ast_scan_cache.json"


def _get_site_package_dirs(project_path: str) -> list[str]:
    """Return possible site-packages directories to inspect for installed packages.

    Checks common virtualenv names in the project (.venv, venv, env) and falls
    back to the current Python environment's site-packages if none found.
    """
    candidates: list[str] = []
    venv_names: list[str] = [".venv", "venv", "env"]
    pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"

    for venv in venv_names:
        venv_path = os.path.join(project_path, venv)
        if not os.path.exists(venv_path):
            continue
        # POSIX layout
        lib_site = os.path.join(venv_path, "lib", pyver, "site-packages")
        lib_site_alt = os.path.join(venv_path, "lib", "site-packages")
        # Windows layout
        win_site = os.path.join(venv_path, "Lib", "site-packages")
        for p in (lib_site, lib_site_alt, win_site):
            if os.path.exists(p):
                candidates.append(p)
    # Fallback to current environment
    try:
        import site as _site

        for p in _site.getsitepackages():
            if os.path.exists(p):
                candidates.append(p)
    except Exception:
        # Last resort: sys.path entries that look like site-packages
        for p in sys.path:
            if p and (p.endswith("site-packages") or p.endswith("dist-packages")):
                if os.path.exists(p):
                    candidates.append(p)

    seen: set[str] = set()
    out: list[str] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _dir_size(path: str) -> int:  # pyright: ignore[reportUnusedFunction]
    """Return total size in bytes for the directory or file at `path`."""
    total = 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except Exception:
            return 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except (OSError, PermissionError) as e:
                # Skip files that cannot be accessed; log when verbose.
                _log(f"Skipped file during size calc: {fp}: {e}", "yellow")
                continue
    return total


def _detect_heavy_hitters_by_size(project_path: str, packages: list[str]) -> list[str]:
    """Analyze installed package sizes and return those exceeding threshold.

    The threshold (in MB) can be configured via the `PYFORGE_HEAVY_HITTER_MB`
    environment variable. Default is 100 MB.
    """
    try:
        threshold_mb = int(os.environ.get("PYFORGE_HEAVY_HITTER_MB", "50"))
    except Exception:
        threshold_mb = 50
    threshold_bytes = threshold_mb * 1024 * 1024

    if not packages:
        return []

    site_dirs = _get_site_package_dirs(project_path)
    if not site_dirs:
        return []

    # Build candidate paths for each package by probing import spec and site-packages
    candidates: dict[str, list[str]] = {pkg: [] for pkg in packages if pkg}

    import importlib.util

    for pkg in list(candidates.keys()):
        try:
            spec = importlib.util.find_spec(pkg)
            if spec:
                # package dir
                if spec.submodule_search_locations:
                    for loc in spec.submodule_search_locations:
                        if os.path.exists(loc):
                            candidates[pkg].append(loc)
                # single-file module
                elif spec.origin and os.path.exists(spec.origin):
                    candidates[pkg].append(spec.origin)
        except Exception as e:
            # ignore import failures but log when verbose
            _log(f"Import probe failed for {pkg}: {e}", "yellow")
            continue

    # fallback: search site-packages for matching names
    for site in site_dirs:
        try:
            for item in os.listdir(site):
                item_l = item.lower()
                for pkg in list(candidates.keys()):
                    pkg_lower = pkg.lower().replace("-", "_")
                    if item_l.startswith(pkg_lower) or item_l == pkg_lower:
                        candidates[pkg].append(os.path.join(site, item))
        except OSError:
            continue

    heavy: list[str] = []
    # Parallel size computation
    path_to_pkg: dict[str, str] = {}
    paths: list[str] = []
    for pkg, pls in candidates.items():
        for p in pls:
            if p and p not in path_to_pkg:
                path_to_pkg[p] = pkg
                paths.append(p)

    # Use parallel utilities for size computation
    sizes = parallel_compute_sizes(paths, max_workers=8)

    # Sum sizes per package
    pkg_sizes: dict[str, int] = {pkg: 0 for pkg in candidates.keys()}
    for p, pkg in path_to_pkg.items():
        pkg_sizes[pkg] = pkg_sizes.get(pkg, 0) + sizes.get(p, 0)

    for pkg, sz in pkg_sizes.items():
        if sz >= threshold_bytes:
            heavy.append(pkg)

    return sorted(heavy)


def _log(message: str, color: str = "blue") -> None:
    verbose = os.environ.get("PYFORGE_VERBOSE") == "1" or os.environ.get("CI") == "true"
    if verbose:
        logutil(message, level="debug", color=color, component="docker_engine")


def _clean_dep_strings(deps: list[str]) -> list[str]:
    """Cleans version constraints (>=, ==, etc.) from dependency strings."""
    cleaned: list[str] = []
    for d in deps:
        name: str = re.split(r"[=><~;\[]", d)[0].strip()
        if name and not name.startswith("#"):
            cleaned.append(name)
    return cleaned


def _get_declared_dependencies(project_path: str) -> list[str] | None:
    """Tries to read explicit dependencies from pyproject.toml or requirements.txt."""
    p_path: str = os.path.join(project_path, "pyproject.toml")
    if os.path.exists(p_path):
        try:
            with open(p_path, encoding="utf-8") as f:
                data: dict[str, Any] = toml.load(f)

                project_section: dict[str, Any] = data.get("project", {})
                deps: Any = project_section.get("dependencies")

                if isinstance(deps, list):
                    str_deps: list[str] = []
                    deps_list: list[Any] = deps  # pyright: ignore[reportUnknownVariableType]
                    for d in deps_list:
                        if isinstance(d, str):
                            str_deps.append(d)

                    return _clean_dep_strings(str_deps)

                poetry_section: dict[str, Any] = data.get("tool", {}).get("poetry", {})
                poetry_deps: Any = poetry_section.get("dependencies")

                if isinstance(poetry_deps, dict):
                    keys: list[str] = []
                    poetry_dict: dict[Any, Any] = poetry_deps  # pyright: ignore[reportUnknownVariableType]
                    for k in poetry_dict.keys():
                        if isinstance(k, str) and k.lower() != "python":
                            keys.append(k)
                    return keys
        except Exception as e:
            _log(f"Could not parse pyproject.toml dependencies: {e}", "yellow")

    req_path: str = os.path.join(project_path, "requirements.txt")
    if os.path.exists(req_path):
        try:
            with open(req_path, encoding="utf-8") as f:
                lines: list[str] = f.readlines()
                return _clean_dep_strings(lines)
        except Exception as e:
            _log(f"Could not parse requirements.txt: {e}", "yellow")

    return None


def get_project_path() -> str:
    """Returns the current working directory."""
    return os.getcwd()


def get_pyproject_path() -> str:
    """Returns the absolute path to pyproject.toml."""
    return os.path.join(get_project_path(), "pyproject.toml")


def get_venv_bin_tools(project_path: str) -> set[str]:
    """
    Scans the venv bin directory for known development tools using a whitelist approach.
    This prevents auxiliary scripts (like doesitcache, dmypy) from breaking the build.
    """
    tools: set[str] = set()
    venv_names: list[str] = [".venv", "venv", "env"]

    known_dev_tools: set[str] = {
        "pytest",
        "ruff",
        "mypy",
        "black",
        "flake8",
        "bandit",
        "isort",
        "pylint",
        "coverage",
        "tox",
        "pre-commit",
        "poetry",
    }

    for venv_name in venv_names:
        venv_path: str = os.path.join(project_path, venv_name)
        if os.path.exists(venv_path):
            bin_path: str = os.path.join(venv_path, "bin")
            scripts_path: str = os.path.join(venv_path, "Scripts")

            target_dir: str | None = None
            if os.path.exists(bin_path):
                target_dir = bin_path
            elif os.path.exists(scripts_path):
                target_dir = scripts_path

            if target_dir:
                try:
                    for item in os.listdir(target_dir):
                        base_name: str = os.path.splitext(item)[0].lower()

                        if base_name in known_dev_tools:
                            tools.add(base_name)
                except OSError as e:
                    _log(f"Could not read venv bin directory: {e}", "yellow")
            break

    return tools


def get_local_modules(project_path: str) -> set[str]:
    """
    Deeply scans the project to identify all local module and package names.
    Ensures internal imports are recognized as local.
    """
    local_names: set[str] = set()
    ignore_dirs: set[str] = {
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".git",
        "build",
        "dist",
    }

    # Parallel directory scanning
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        for d in dirs:
            dir_path: str = os.path.join(root, d)
            try:
                # Check if directory is a package or contains python files
                if any(f.endswith(".py") for f in os.listdir(dir_path)):
                    local_names.add(d)
            except OSError as e:
                _log(f"Could not scan directory {dir_path}: {e}", "yellow")
                continue

        for f in files:
            if f.endswith(".py") and f != "__init__.py":
                module_name: str = f.rsplit(".", 1)[0]
                local_names.add(module_name)

    return local_names


def get_imports(project_path: str) -> set[str]:
    """Recursively parses all .py files to extract imported module names."""
    imports: set[str] = set()

    # Parallel file scanning
    def is_python_file(path: str) -> bool:
        return path.endswith(".py")

    all_py_files = parallel_scan_files(project_path, is_python_file)

    # Parallel AST parsing
    parsed_files = parallel_parse_files(all_py_files, max_workers=8)

    # Extract imports from AST
    for _file_path, tree in parsed_files.items():
        if tree is None:
            continue

        try:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module.split(".")[0])
        except Exception:  # nosec B112
            continue

    return imports


def get_clean_final_list(
    detected_imports: set[str], dev_tools: set[str], project_path: str
) -> list[str]:
    """
    Filters out stdlibs, local modules, and boilerplate.
    """

    std_libs: set[str] = set()
    if hasattr(sys, "stdlib_module_names"):
        std_libs.update(sys.stdlib_module_names)

    # Common fallbacks for Pylance/older versions
    std_libs.update(
        {
            "sys",
            "os",
            "pathlib",
            "re",
            "ast",
            "json",
            "subprocess",
            "typing",
            "collections",
            "shutil",
            "unittest",
            "argparse",
            "urllib",
            "abc",
        }
    )

    local_modules: set[str] = get_local_modules(project_path)
    combined: set[str] = set(detected_imports) | set(dev_tools)
    final: list[str] = []

    for item in combined:
        item_lower: str = item.lower().replace("-", "_")

        if (
            item not in std_libs
            and item not in local_modules
            and not item.startswith("_")
            and item_lower
            not in {"setup", "setuptools", "pkg_resources", "pip", "python"}
        ):
            final.append(item)

    return sorted(list(set(final)))


def _get_ast_cache_ttl() -> int:
    """Return AST cache TTL in seconds from environment."""
    try:
        return max(0, int(os.environ.get("PYFORGE_AST_CACHE_TTL", "300")))
    except Exception:
        return 300


def _get_cache_dir(project_path: str) -> str:
    """Return absolute cache directory path for project."""
    return os.path.join(project_path, _CACHE_DIR_NAME)


def _get_ast_cache_file(project_path: str) -> str:
    """Return absolute AST cache file path for project."""
    return os.path.join(_get_cache_dir(project_path), _AST_CACHE_FILE_NAME)


def _build_dependency_signature(project_path: str) -> str:
    """Build a stable signature for dependency-relevant project state."""
    files_to_hash: list[str] = []

    # Top-level dependency definition files.
    for name in ["pyproject.toml", "requirements.txt", "requirements-dev.txt"]:
        path = os.path.join(project_path, name)
        if os.path.exists(path):
            files_to_hash.append(path)

    # Python sources (same scan pattern as import detection).
    py_files = parallel_scan_files(project_path, lambda path: path.endswith(".py"))
    files_to_hash.extend(py_files)

    records: list[str] = []
    for path in sorted(set(files_to_hash)):
        try:
            st = os.stat(path)
            rel = os.path.relpath(path, project_path)
            records.append(f"{rel}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            continue

    threshold = os.environ.get("PYFORGE_HEAVY_HITTER_MB", "50")
    records.append(f"heavy-threshold:{threshold}")
    joined = "|".join(records)
    return sha256(joined.encode("utf-8")).hexdigest()


def _load_ast_cache(project_path: str) -> dict[str, Any]:
    """Load AST cache metadata from disk."""
    from typing import cast

    cache_file = _get_ast_cache_file(project_path)
    if not os.path.exists(cache_file):
        return {}
    try:
        with open(cache_file, encoding="utf-8") as f:
            data: Any = json.load(f)
            return cast(dict[str, Any], data) if isinstance(data, dict) else {}
    except Exception as e:
        _log(f"Failed to read AST cache: {e}", "yellow")
        return {}


def _write_ast_cache(project_path: str, payload: dict[str, Any]) -> None:
    """Write AST cache metadata to disk."""
    try:
        cache_dir = _get_cache_dir(project_path)
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = _get_ast_cache_file(project_path)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
    except Exception as e:
        _log(f"Failed to write AST cache: {e}", "yellow")


def _load_cached_dependency_report(
    project_path: str, signature: str, ttl_seconds: int
) -> dict[str, Any] | None:
    """Load cached dependency report if signature and TTL are valid."""
    cache = _load_ast_cache(project_path)
    cache_signature = cache.get("signature")
    created_at = cache.get("created_at")
    report = cache.get("report")

    if (
        not isinstance(cache_signature, str)
        or cache_signature != signature
        or not isinstance(created_at, int | float)
        or not isinstance(report, dict)
    ):
        return None

    age = time.time() - float(created_at)
    if ttl_seconds > 0 and age > ttl_seconds:
        return None

    _log("Using cached dependency report from .pyforge-deploy-cache", "green")
    return cast(dict[str, Any], report)


def _store_dependency_report_cache(
    project_path: str,
    signature: str,
    report: dict[str, Any],
) -> None:
    """Persist dependency report cache to disk."""
    _write_ast_cache(
        project_path,
        {
            "signature": signature,
            "created_at": time.time(),
            "report": report,
        },
    )


def detect_dependencies(project_path: str) -> dict[str, Any]:
    """
    Main entry point for dependency detection.
    Uses parallelization for AST analysis and size computation.
    """
    signature = _build_dependency_signature(project_path)
    ttl_seconds = _get_ast_cache_ttl()

    cached = _load_cached_dependency_report(project_path, signature, ttl_seconds)
    if cached is not None:
        return cached

    report: dict[str, Any] = {
        "has_pyproject": os.path.exists(os.path.join(project_path, "pyproject.toml")),
        "requirement_files": [],
        "detected_imports": [],
        "dev_tools": sorted(list(get_venv_bin_tools(project_path))),
        "final_list": [],
        "heavy_hitters": [],
        "source": "unknown",
    }

    for req_file in ["requirements.txt", "requirements-dev.txt"]:
        if os.path.exists(os.path.join(project_path, req_file)):
            report["requirement_files"].append(req_file)

    declared_deps = _get_declared_dependencies(project_path)

    if declared_deps:
        report["final_list"] = sorted(list(set(declared_deps)))
        report["source"] = "declared"
        _log(
            "Using declared dependencies from pyproject.toml or requirements.txt",
            "cyan",
        )
    else:
        # Parallel AST analysis for faster import detection
        raw_imports: set[str] = get_imports(project_path)
        raw_tools: set[str] = set(report["dev_tools"])
        final_cleaned: list[str] = get_clean_final_list(
            raw_imports, raw_tools, project_path
        )
        report["detected_imports"] = sorted(list(raw_imports))
        report["final_list"] = final_cleaned
        report["source"] = "ast_fallback"
        _log(
            "AST source code scan completed (parallel analysis).",
            "cyan",
        )

    # Identify heavy-hitter packages using parallel size computation
    heavy_candidates = {
        "numpy",
        "pandas",
        "scipy",
        "tensorflow",
        "torch",
        "torchvision",
        "pillow",
        "opencv-python",
        "scikit-image",
        "scikit-learn",
    }

    candidates = report.get("final_list", [])

    # Parallel heavy hitter detection
    heavy_by_size = _detect_heavy_hitters_by_size(project_path, candidates)

    if heavy_by_size:
        heavy_hitters = heavy_by_size
        remaining = [p for p in candidates if p not in heavy_hitters]
    else:
        heavy_hitters = [p for p in candidates if p and p.lower() in heavy_candidates]
        remaining = [p for p in candidates if p not in heavy_hitters]

    report["heavy_hitters"] = sorted(set(heavy_hitters))
    report["final_list"] = sorted(set(remaining))

    _store_dependency_report_cache(project_path, signature, report)

    return report


def get_python_version() -> str:
    """
    Detects Python version from pyproject.toml or returns system default.
    """
    default_v: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    try:
        p_path: str = get_pyproject_path()
        if os.path.exists(p_path):
            with open(p_path, encoding="utf-8") as f:
                data: dict[str, Any] = toml.load(f)
                requires_python: str | None = data.get("project", {}).get(
                    "requires-python"
                )
                if requires_python:
                    match = re.search(r"(\d+\.\d+)", requires_python)
                    if match:
                        return match.group(1)
    except Exception as err:
        _log(f"Failed to detect python version from pyproject.toml: {err}", "yellow")
    return default_v


def _entry_point_from_pyproject_scripts(project_path: str) -> str | None:
    """Return entry-point path from [project.scripts] when available.

    Prefers returning an existing file path. For src-layout projects, maps
    ``package.module`` to ``src/package/module.py`` when that file exists.
    """
    pyproject_path = os.path.join(project_path, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        return None

    try:
        with open(pyproject_path, encoding="utf-8") as f:
            data: dict[str, Any] = toml.load(f)
    except Exception:
        return None

    scripts_obj: Any = data.get("project", {}).get("scripts", {})
    if not isinstance(scripts_obj, dict) or not scripts_obj:
        return None

    first_script = next(
        (
            value
            for value in scripts_obj.values()  # pyright: ignore[reportUnknownVariableType]
            if isinstance(value, str) and value.strip()
        ),
        None,
    )
    if first_script is None:
        return None

    module_part = first_script.split(":", 1)[0].strip()
    if not module_part:
        return None

    module_path = module_part.replace(".", "/") + ".py"
    src_candidate = os.path.join(project_path, "src", module_path)
    root_candidate = os.path.join(project_path, module_path)

    if os.path.exists(src_candidate):
        return os.path.join("src", module_path)
    if os.path.exists(root_candidate):
        return module_path
    # If file is not present, still return the inferred path for consistency.
    return module_path


def _contains_main_guard(content: str) -> bool:
    """Return True when file content has a standard __main__ guard."""
    return (
        'if __name__ == "__main__":' in content
        or "if __name__ == '__main__':" in content
    )


def _is_ignored_for_entry_scan(rel_path: str) -> bool:
    """Filter out directories that should not participate in entry detection."""
    parts = rel_path.split(os.sep)
    ignored = {
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".git",
        "build",
        "dist",
        "tests",
        "docs",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "node_modules",
        "wheels",
    }
    return any(part in ignored for part in parts)


def detect_entry_point(project_path: str) -> str | None:
    """
    Attempts to auto-detect the application's entry point (main executable file).
    Looks for common names like app.py, main.py, or scans for __main__ blocks.
    """
    _log("Scanning project for entry point...", "cyan")

    pyproject_entry = _entry_point_from_pyproject_scripts(project_path)
    if pyproject_entry:
        _log(f"Found entry point from pyproject scripts: {pyproject_entry}", "green")
        return pyproject_entry

    direct_candidates = [
        "src/cli.py",
        "src/main.py",
        "src/app.py",
        "main.py",
        "app.py",
        "run.py",
    ]
    for candidate in direct_candidates:
        if os.path.exists(os.path.join(project_path, candidate)):
            _log(f"Found standard entry point file: {candidate}", "green")
            return candidate

    all_py_files = parallel_scan_files(
        project_path,
        lambda path: path.endswith(".py"),
    )

    filtered_py_files: list[str] = []
    for file_path in all_py_files:
        rel = os.path.relpath(file_path, project_path)
        if _is_ignored_for_entry_scan(rel):
            continue
        filtered_py_files.append(file_path)

    # Fast path: choose best-named candidate without opening files.
    preferred_order = {
        "cli.py": 0,
        "main.py": 1,
        "app.py": 2,
        "__main__.py": 3,
        "run.py": 4,
    }
    best_named: tuple[int, int, int, str] | None = None
    for file_path in filtered_py_files:
        filename = os.path.basename(file_path)
        if filename not in preferred_order:
            continue
        rel = os.path.relpath(file_path, project_path)
        if rel in {"setup.py", "__init__.py", "__about__.py"}:
            continue
        rel_parts = rel.split(os.sep)
        depth = len(rel_parts)
        src_bias = 0 if rel_parts and rel_parts[0] == "src" else 1
        score = (preferred_order[filename], src_bias, depth, rel)
        if best_named is None or score < best_named:
            best_named = score

    if best_named is not None:
        detected = best_named[3]
        _log(f"Found CLI-like entry point by filename: {detected}", "green")
        return detected

    # Fallback: content-based detection (parallelized read).
    scan_targets = [
        file_path
        for file_path in filtered_py_files
        if os.path.basename(file_path)
        not in {"setup.py", "__init__.py", "__about__.py"}
    ]
    file_contents = parallel_read_files(scan_targets, max_workers=8)

    candidates_with_main: list[str] = []
    for file_path, content in file_contents.items():
        if content is None:
            continue
        if _contains_main_guard(content):
            candidates_with_main.append(os.path.relpath(file_path, project_path))

    if candidates_with_main:
        candidates_with_main.sort(
            key=lambda rel: (
                0 if rel.startswith(f"src{os.sep}") else 1,
                rel.count(os.sep),
                rel,
            )
        )
        detected = candidates_with_main[0]
        _log(
            f"Auto-detected runnable script via __main__ block: {detected}",
            "green",
        )
        return detected

    _log("No clear entry point detected.", "yellow")
    return None
