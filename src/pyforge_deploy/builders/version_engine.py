import json
import os
import re
from typing import cast
from urllib.request import urlopen

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
    try:
        return os.path.join(get_project_path(), "pyproject.toml")
    except Exception as e:
        print(f"Error accessing pyproject.toml: {e}")
        raise FileNotFoundError(f"Error accessing pyproject.toml: {e}") from e


def get_cache_path(project_path: str, project_name: str) -> str:
    """
    Returns the path to the version cache or about file.
    """
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
    """
    Parses pyproject.toml for project name and version.
    Handles TOML tables and lists for dynamic versioning.
    """

    pyproject_path = get_pyproject_path()
    if not os.path.exists(pyproject_path):
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_path}")
    try:
        data = toml.load(pyproject_path)
    except Exception as e:
        print(f"Error parsing pyproject.toml: {e}")
        raise
    project = data.get("project", {})
    project_name = project.get("name")
    project_version = project.get("version")
    dynamic = project.get("dynamic", [])
    if not project_name:
        raise ValueError("Could not find project name in pyproject.toml")
    if isinstance(dynamic, list) and "version" in dynamic:
        return project_name, "dynamic"
    if project_version is None:
        raise ValueError(
            "Could not find static version, and dynamic versioning is not enabled."
        )
    return project_name, project_version


def fetch_latest_version(project_name: str, timeout: float = 5.0) -> str | None:
    """
    Fetches the latest version from PyPI. Logs errors and retries once if failed.
    """
    url = f"https://pypi.org/pypi/{project_name}/json"
    if not url.startswith("https://"):
        print(f"Invalid URL: {url}")
        return None
    for attempt in range(2):
        try:
            with urlopen(url, timeout=timeout) as response:  # nosec B310
                status = getattr(response, "status", 200)
                if status != 200:
                    print(f"PyPI request failed with status {status}")
                    continue
                data = json.loads(response.read().decode("utf-8"))
                return cast(str, data.get("info", {}).get("version"))
        except Exception as e:
            print(f"PyPI fetch error (attempt {attempt + 1}): {e}")
    return None


def write_version_cache(cache_path: str, version: str) -> None:
    """
    Writes the version string to the cache file.
    """
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(version)
    except Exception as e:
        print(f"Error writing version cache: {e}")


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
        print(f"Malformed version string: {current_version}")
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
        print(f"Cache file not found: {cache_path}")
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        print(f"Error reading cache file: {e}")
        return None
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if match:
        return match.group(1)
    if content and content[0].isdigit():
        return content
    print(f"Malformed cache content: {content}")
    return None


def write_both_caches(project_path: str, project_name: str, version: str) -> None:
    """
    Writes version to both cache and about file. Creates directory if missing.
    """
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
            print(f"Error creating about directory: {e}")
            return
    try:
        with open(about_path, "w", encoding="utf-8") as f:
            f.write(f'__version__ = "{version}"\n')
    except Exception as e:
        print(f"Error writing about file: {e}")


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
        print(f"Warning: {e}. Falling back to 0.0.0")
        return "0.0.0"
    if project_version != "dynamic":
        return project_version
    project_path = get_project_path()
    if MANUAL_VERSION is not None:
        write_both_caches(project_path, project_name, MANUAL_VERSION)
        return MANUAL_VERSION
    cache_path = get_cache_path(project_path, project_name)
    cached_version = read_local_version(cache_path)
    pypi_version = fetch_latest_version(project_name)
    base_version = "0.0.0"
    try:
        from packaging.version import Version

        if pypi_version and cached_version:
            try:
                base_version = (
                    pypi_version
                    if Version(pypi_version) > Version(cached_version)
                    else cached_version
                )
            except Exception as e:
                print(f"Version comparison error: {e}")
                base_version = pypi_version or cached_version or "0.0.0"
        else:
            base_version = pypi_version or cached_version or "0.0.0"
    except ImportError:
        print("packaging module not found, using string comparison for versions.")
        base_version = pypi_version or cached_version or "0.0.0"
    next_version = calculate_next_version(base_version, BUMP_TYPE or "patch")
    if AUTO_INCREMENT or (BUMP_TYPE and BUMP_TYPE in {"major", "minor", "patch"}):
        write_both_caches(project_path, project_name, next_version)
        return next_version
    return base_version
