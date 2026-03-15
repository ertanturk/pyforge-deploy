import ast
import os
import re
import sys
from typing import Any

import toml

from pyforge_deploy.colors import color_text


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
            print(f"[WARNING] Could not parse pyproject.toml dependencies: {e}")

    req_path: str = os.path.join(project_path, "requirements.txt")
    if os.path.exists(req_path):
        try:
            with open(req_path, encoding="utf-8") as f:
                lines: list[str] = f.readlines()
                return _clean_dep_strings(lines)
        except Exception as e:
            print(f"[WARNING] Could not parse requirements.txt: {e}")

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
                    from pyforge_deploy.colors import color_text

                    print(
                        color_text(
                            f"Warning: Could not read venv bin directory: {e}", "yellow"
                        )
                    )
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

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        for d in dirs:
            dir_path: str = os.path.join(root, d)
            try:
                # Check if directory is a package or contains python files
                if any(f.endswith(".py") for f in os.listdir(dir_path)):
                    local_names.add(d)
            except OSError as e:
                from pyforge_deploy.colors import color_text

                print(
                    color_text(
                        f"Warning: Could not scan directory {dir_path}: {e}", "yellow"
                    )
                )
                continue

        for f in files:
            if f.endswith(".py") and f != "__init__.py":
                module_name: str = f.rsplit(".", 1)[0]
                local_names.add(module_name)

    return local_names


def get_imports(project_path: str) -> set[str]:
    """Recursively parses all .py files to extract imported module names."""
    imports: set[str] = set()
    ignore_dirs: set[str] = {
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

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        for file in files:
            if file.endswith(".py"):
                file_path: str = os.path.join(root, file)
                try:
                    with open(file_path, "rb") as f:
                        content_bytes = f.read()
                        if not content_bytes.strip():
                            continue
                        tree: ast.AST = ast.parse(content_bytes, filename=file_path)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                imports.add(alias.name.split(".")[0])
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                imports.add(node.module.split(".")[0])
                except (SyntaxError, OSError):
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
    combined: set[str] = detected_imports
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
        from pyforge_deploy.colors import color_text

        if os.environ.get("GITHUB_ACTIONS") != "true":
            print(
                color_text(
                    (
                        "Using declared dependencies from "
                        "pyproject.toml or requirements.txt"
                    ),
                    "cyan",
                )
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
        from pyforge_deploy.colors import color_text

        if os.environ.get("GITHUB_ACTIONS") != "true":
            print(
                color_text(
                    "No declared dependencies found. "
                    "Falling back to AST source code scan.",
                    "yellow",
                )
            )

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
        print(
            color_text(
                f"[WARNING] Failed to detect python version from pyproject.toml: {err}",
                "yellow",
            )
        )
    return default_v
