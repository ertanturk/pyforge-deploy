import ast
import os
import re
import sys
from typing import Any

import toml

from pyforge_deploy.builders.parallel import (
    parallel_compute_sizes,
    parallel_parse_files,
    parallel_scan_files,
)
from pyforge_deploy.colors import color_text


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
        print(color_text(f"[docker_engine] {message}", color))


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


def detect_dependencies(project_path: str) -> dict[str, Any]:
    """
    Main entry point for dependency detection.
    Uses declared dependencies if available, otherwise falls back to AST.
    """
    report: dict[str, Any] = {
        "has_pyproject": os.path.exists(os.path.join(project_path, "pyproject.toml")),
        "requirement_files": [],
        "detected_imports": [],
        "dev_tools": sorted(list(get_venv_bin_tools(project_path))),
        "final_list": [],
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
        raw_imports: set[str] = get_imports(project_path)
        raw_tools: set[str] = set(report["dev_tools"])
        final_cleaned: list[str] = get_clean_final_list(
            raw_imports, raw_tools, project_path
        )
        report["detected_imports"] = sorted(list(raw_imports))
        report["final_list"] = final_cleaned
        report["source"] = "ast_fallback"
        _log(
            "No declared dependencies found. Falling back to AST source code scan.",
            "yellow",
        )

    # Identify heavy-hitter packages that should be installed in a dedicated
    # layer for better caching. Separate them out from the main final_list.
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

    # Prefer to detect heavy packages by inspecting installed package sizes
    # in the project's virtualenv or site-packages. Fall back to name-based
    # matching for environments where we cannot inspect installed packages.
    candidates = report.get("final_list", [])
    heavy_by_size = _detect_heavy_hitters_by_size(project_path, candidates)

    if heavy_by_size:
        heavy_hitters = heavy_by_size
        remaining = [p for p in candidates if p not in heavy_hitters]
    else:
        # Fallback: use static candidate list
        heavy_hitters = [p for p in candidates if p and p.lower() in heavy_candidates]
        remaining = [p for p in candidates if p not in heavy_hitters]

    report["heavy_hitters"] = sorted(set(heavy_hitters))
    report["final_list"] = sorted(set(remaining))

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


def detect_entry_point(project_path: str) -> str | None:
    """
    Attempts to auto-detect the application's entry point (main executable file).
    Looks for common names like app.py, main.py, or scans for __main__ blocks.
    """
    _log("Scanning project for entry point...", "cyan")

    candidates = ["main.py", "app.py", "src/main.py", "src/app.py", "run.py"]
    for cand in candidates:
        if os.path.exists(os.path.join(project_path, cand)):
            _log(f"Found standard entry point file: {cand}", "green")
            return cand

    ignore_dirs = {
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".git",
        "build",
        "dist",
        "tests",
        "docs",
    }

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        for file in files:
            if file.endswith(".py") and file not in {
                "setup.py",
                "__init__.py",
                "__about__.py",
            }:
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()
                        if (
                            'if __name__ == "__main__":' in content
                            or "if __name__ == '__main__':" in content
                        ):
                            rel_path = os.path.relpath(file_path, project_path)
                            _log(
                                f"Auto-detected runnable script via __main__ block: {rel_path}",  # noqa: E501
                                "green",
                            )
                            return rel_path
                except Exception:  # nosec B112
                    continue

    _log("No clear entry point detected.", "yellow")
    return None
