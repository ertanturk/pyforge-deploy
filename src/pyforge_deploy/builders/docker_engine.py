import os
import re
import subprocess  # nosec B404: Used safely for trusted commands only
import sys
from typing import Any

import toml


def get_project_path() -> str:
    """
    Returns the current working directory as the project path.
    """
    return os.getcwd()


def get_pyproject_path() -> str:
    """
    Returns the path to pyproject.toml in the project directory.
    """
    return os.path.join(get_project_path(), "pyproject.toml")


def detect_dependencies(project_path: str) -> dict[str, Any]:
    """
    Detects the presence of pyproject.toml and requirements files in the project
    directory. Falls back to `pip freeze` if no dependency files are found.
    """
    report: dict[str, Any] = {"has_pyproject": False, "requirement_files": []}

    pyproject_path = os.path.join(project_path, "pyproject.toml")
    if os.path.exists(pyproject_path):
        report["has_pyproject"] = True

    req_path = os.path.join(project_path, "requirements.txt")
    req_dev_path = os.path.join(project_path, "requirements-dev.txt")

    if os.path.exists(req_path):
        report["requirement_files"].append("requirements.txt")
    if os.path.exists(req_dev_path):
        report["requirement_files"].append("requirements-dev.txt")

    if not report["has_pyproject"] and not report["requirement_files"]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "freeze"],  # nosec B603: Command is static and trusted
                capture_output=True,
                text=True,
                check=True,
            )
            frozen_deps = result.stdout.strip()

            if frozen_deps:
                frozen_file_name = "requirements-frozen.txt"
                frozen_path = os.path.join(project_path, frozen_file_name)

                with open(frozen_path, "w", encoding="utf-8") as f:
                    f.write(frozen_deps + "\n")
                report["requirement_files"].append(frozen_file_name)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to run pip freeze: {e.stderr}") from e
    return report


def parse_pyproject() -> dict[str, Any] | None:
    """
    Parses the pyproject.toml file and returns its contents as a dictionary.
    Returns None if the file does not exist or parsing fails.
    """
    pyproject_path = get_pyproject_path()
    if not os.path.exists(pyproject_path):
        return None
    try:
        return toml.load(pyproject_path)
    except Exception:
        return None


def get_python_version() -> str:
    """Determines the minimum Python version required
    by the project based on the pyproject.toml file.
    If the file is missing or does not specify a version,
    defaults to the current Python version."""
    default_version = f"{sys.version_info.major}.{sys.version_info.minor}"

    pyproject_data = parse_pyproject()
    if not pyproject_data:
        return default_version

    requires_python = pyproject_data.get("project", {}).get("requires-python")
    if not requires_python:
        return default_version

    version_match = re.search(r"(\d+\.\d+)", requires_python)
    if version_match:
        return version_match.group(1)

    return default_version
