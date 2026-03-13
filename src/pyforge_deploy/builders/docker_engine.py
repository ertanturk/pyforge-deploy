import ast
import os
import re
import sys
from typing import Any

import toml


def get_project_path() -> str:
    """Returns the current working directory."""
    return os.getcwd()


def get_pyproject_path() -> str:
    """Returns the absolute path to pyproject.toml."""
    return os.path.join(get_project_path(), "pyproject.toml")


def get_venv_bin_tools(project_path: str) -> set[str]:
    """
    Scans the venv bin directory for tools, filtering out core Python binaries.
    """
    tools: set[str] = set()
    venv_names: list[str] = [".venv", "venv", "env"]

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
                    ignore_list: set[str] = {
                        "python",
                        "python3",
                        "pip",
                        "pip3",
                        "pip3.12",
                        "activate",
                        "activate.bat",
                        "activate.ps1",
                        "deactivate",
                        "wheel",
                        "easy_install",
                    }
                    blacklist_prefixes: tuple[str, ...] = (
                        "rst2",
                        "jupyter-",
                        "python",
                        "pip",
                    )

                    for item in os.listdir(target_dir):
                        base_name: str = os.path.splitext(item)[0].lower()
                        if base_name not in ignore_list and not base_name.startswith(
                            blacklist_prefixes
                        ):
                            tools.add(base_name)
                except OSError as e:
                    print(f"Warning: Could not read venv bin directory. {e}")
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
            except OSError:
                continue

        for f in files:
            if f.endswith(".py") and f != "__init__.py":
                module_name: str = f.rsplit(".", 1)[0]
                local_names.add(module_name)

    return local_names


def get_imports(project_path: str) -> set[str]:
    """
    Detects all unique top-level imports using AST analysis.
    """
    imports: set[str] = set()
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
        dirs[:] = [d for d in dirs if d not in ignore_dirs]

        for file in files:
            if file.endswith(".py"):
                file_path: str = os.path.join(root, file)
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content: str = f.read()
                        if not content.strip():
                            continue
                        tree: ast.AST = ast.parse(content, filename=file_path)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                imports.add(alias.name.split(".")[0])
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                imports.add(node.module.split(".")[0])
                except (SyntaxError, UnicodeDecodeError, OSError):
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
    combined: set[str] = detected_imports | dev_tools
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
    """
    raw_imports: set[str] = get_imports(project_path)
    raw_tools: set[str] = get_venv_bin_tools(project_path)
    final_cleaned: list[str] = get_clean_final_list(
        raw_imports, raw_tools, project_path
    )

    report: dict[str, Any] = {
        "has_pyproject": os.path.exists(os.path.join(project_path, "pyproject.toml")),
        "requirement_files": [],
        "detected_imports": sorted(list(raw_imports)),
        "dev_tools": sorted(list(raw_tools)),
        "final_list": final_cleaned,
    }

    for req_file in ["requirements.txt", "requirements-dev.txt"]:
        if os.path.exists(os.path.join(project_path, req_file)):
            report["requirement_files"].append(req_file)

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
        print(f"[WARNING] Failed to detect python version from pyproject.toml: {err}")
    return default_v
