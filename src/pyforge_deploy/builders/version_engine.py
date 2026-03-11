import json
import os
import re
import sys
from typing import cast
from urllib.request import urlopen

import toml
from packaging.version import Version

from pyforge_deploy.colors import color_text


def find_project_root(current_path: str) -> str:
    """Search upwards for pyproject.toml to determine project root."""
    path = os.path.abspath(current_path)
    while path and path != os.path.dirname(path):
        if os.path.exists(os.path.join(path, "pyproject.toml")):
            return path
        path = os.path.dirname(path)
    return os.getcwd()


def get_project_path() -> str:
    """Return the project root path (searches upwards)."""
    return find_project_root(os.getcwd())


def get_pyproject_path() -> str:
    return os.path.join(get_project_path(), "pyproject.toml")


def get_cache_path(project_path: str, project_name: str) -> str:
    cache_path = os.path.join(project_path, ".version_cache")
    package_name = project_name.replace("-", "_")
    about_path = os.path.join(project_path, "src", package_name, "__about__.py")
    if os.path.exists(cache_path) and os.path.exists(about_path):
        if os.path.getmtime(about_path) > os.path.getmtime(cache_path):
            return about_path
        return cache_path
    if os.path.exists(about_path):
        return about_path
    return cache_path


def get_project_details() -> tuple[str, str]:
    root = find_project_root(os.getcwd())
    pyproject_path = os.path.join(root, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_path}")
    data = toml.load(pyproject_path)
    project = data.get("project", {})
    name = project.get("name")
    version = project.get("version")
    dynamic = project.get("dynamic", [])
    if not name:
        raise ValueError("Project name missing in pyproject.toml")
    if isinstance(dynamic, list) and "version" in dynamic:
        return name, "dynamic"
    return name, version or "0.0.0"


def fetch_latest_version(project_name: str, timeout: float = 5.0) -> str | None:
    url = f"https://pypi.org/pypi/{project_name}/json"
    if not url.startswith("https://"):
        print(color_text(f"Invalid URL: {url}", "yellow"))
        return None
    for attempt in range(2):
        try:
            with urlopen(url, timeout=timeout) as response:  # nosec B310
                status = getattr(response, "status", 200)
                if status != 200:
                    print(
                        color_text(
                            f"PyPI request failed with status {status}", "yellow"
                        )
                    )
                    continue
                data = json.loads(response.read().decode("utf-8"))
                return cast(str, data.get("info", {}).get("version"))
        except Exception as e:
            print(
                color_text(f"PyPI fetch error (attempt {attempt + 1}): {e}", "yellow")
            )
    return None


def write_version_cache(cache_path: str, version: str) -> None:
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(version)
    except Exception as e:
        print(color_text(f"Error writing version cache: {e}", "red"))


def calculate_next_version(current_version: str, bump_type: str = "patch") -> str:
    """
    Calculates the next version given bump type. Logs malformed input.
    """
    parts = current_version.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2])
    except ValueError:
        print(color_text(f"Malformed version string: {current_version}", "red"))
        raise ValueError(
            f"Cannot auto-increment malformed version: {current_version}"
        ) from None
    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "patch":
        patch += 1
    else:
        print(f"Invalid bump_type: {bump_type}")
        raise ValueError("bump_type must be 'major', 'minor', or 'patch'")
    return f"{major}.{minor}.{patch}"


def read_local_version(cache_path: str) -> str | None:
    """
    Reads the local version from cache or about file. Logs malformed content.
    """
    if not os.path.exists(cache_path):
        print(color_text(f"Cache file not found: {cache_path}", "yellow"))
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        print(color_text(f"Error reading cache file: {e}", "red"))
        return None
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if match:
        return match.group(1)
    if content and content[0].isdigit():
        return content
    print(color_text(f"Malformed cache content: {content}", "yellow"))
    return None


def write_both_caches(project_path: str, project_name: str, version: str) -> None:
    cache_path = os.path.join(project_path, ".version_cache")
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(version)
    except Exception as e:
        print(f"Error writing cache: {e}")
    package_name = project_name.replace("-", "_")
    about_path = os.path.join(project_path, "src", package_name, "__about__.py")
    about_dir = os.path.dirname(about_path)
    if not os.path.exists(about_dir):
        try:
            os.makedirs(about_dir)
        except Exception as e:
            print(color_text(f"Error creating about directory: {e}", "red"))
            return
    try:
        with open(about_path, "w", encoding="utf-8") as f:
            f.write(f'__version__ = "{version}"\n')
    except Exception as e:
        print(color_text(f"Error writing about file: {e}", "red"))


def get_dynamic_version(
    MANUAL_VERSION: str | None = None,
    BUMP_TYPE: str | None = None,
    AUTO_INCREMENT: bool = False,
) -> str:
    """
    Determines the dynamic version, handling manual, bump, and auto-increment.
    Logs errors and handles packaging fallback.
    """
    try:
        project_name, project_version = get_project_details()
    except Exception as e:
        print(color_text(f"Warning: {e}. Falling back to 0.0.0", "yellow"))
        return "0.0.0"

    if project_version != "dynamic" and MANUAL_VERSION is None:
        return project_version

    root = find_project_root(os.getcwd())
    if MANUAL_VERSION is not None:
        write_both_caches(root, project_name, MANUAL_VERSION)
        return MANUAL_VERSION

    # Gather candidate sources for cached/about versions
    package_name = project_name.replace("-", "_")
    candidates = [
        os.path.join(root, "src", package_name, "__about__.py"),
        os.path.join(root, package_name, "__about__.py"),
        os.path.join(root, ".version_cache"),
    ]
    cached_version = None
    for candidate in candidates:
        cached_version = read_local_version(candidate)
        if cached_version:
            break

    pypi_version = fetch_latest_version(project_name)
    base_version = "0.0.0"
    if pypi_version and cached_version:
        try:
            base_version = (
                pypi_version
                if Version(pypi_version) > Version(cached_version)
                else cached_version
            )
        except Exception as e:
            print(color_text(f"Version comparison error: {e}", "yellow"))
            base_version = pypi_version or cached_version or "0.0.0"
    else:
        base_version = pypi_version or cached_version or "0.0.0"

    next_version = calculate_next_version(base_version, BUMP_TYPE or "patch")
    if AUTO_INCREMENT or (BUMP_TYPE and BUMP_TYPE in {"major", "minor", "patch"}):
        write_both_caches(root, project_name, next_version)
        return next_version
    return base_version


# Expose module under test-friendly alias used by tests
sys.modules.setdefault(
    "src.pyforge_deploy.builders.version_engine", sys.modules[__name__]
)
