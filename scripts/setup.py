"""Dynamic version setup for the project."""

import json
import os
import re
from pathlib import Path
from typing import cast
from urllib.request import urlopen

from setuptools import setup

try:
    from packaging.version import InvalidVersion, Version

    HAS_PACKAGING = True
except ImportError:
    HAS_PACKAGING = False
    Version = None  # type: ignore[misc,assignment]
    InvalidVersion = Exception  # type: ignore[misc,assignment]

BASE_DIR = (
    Path(__file__).parent.parent if "scripts" in __file__ else Path(__file__).parent
)
PYPROJECT_PATH = BASE_DIR / "pyproject.toml"
CACHE_PATH = BASE_DIR / ".version_cache"
ABOUT_PATH = BASE_DIR / "src" / "pyforge_deploy" / "__about__.py"

IS_INITIAL_PUBLISH = True
ENV_SKIP_PYPI = os.environ.get("PYFORGE_SKIP_PYPI", "0") == "1"
ENV_ALLOW_AUTO_INCREMENT = os.environ.get("PYFORGE_AUTO_INCREMENT", "0") == "1"

_computed_version = None


def read_project_name() -> str:
    if not PYPROJECT_PATH.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {PYPROJECT_PATH}")
    with open(PYPROJECT_PATH, encoding="utf-8") as f:
        for line in f:
            if line.startswith("name ="):
                return line.split("=")[1].strip().strip('"')
    raise ValueError("Project name not found in pyproject.toml")


def read_internal_cache_version() -> str | None:
    try:
        if CACHE_PATH.exists():
            content = CACHE_PATH.read_text(encoding="utf-8").strip()
            return content if content else None
    except (OSError, ValueError):
        return None
    return None


def fetch_latest_version(project_name: str, timeout: float = 5.0) -> str | None:
    if ENV_SKIP_PYPI:
        return None
    url = f"https://pypi.org/pypi/{project_name}/json"
    if not url.startswith("https://"):
        return None
    try:
        with urlopen(url, timeout=timeout) as response:  # nosec B310
            status = getattr(response, "status", 200)
            if status != 200:
                return None
            data = json.loads(response.read().decode("utf-8"))
            return cast(str, data.get("info", {}).get("version"))
    except Exception:
        return None


def write_version_cache(version: str) -> None:
    CACHE_PATH.write_text(version, encoding="utf-8")


def update_about_py(version: str) -> None:
    try:
        ABOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        ABOUT_PATH.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to update __about__.py: {e}")


def calculate_next_version(latest_version: str) -> str:
    if HAS_PACKAGING:
        try:
            v = Version(latest_version)
            parts = list(v.release)
            parts[-1] += 1
            return ".".join(str(p) for p in parts)
        except InvalidVersion:
            pass

    nums = re.findall(r"\d+", latest_version)
    if not nums:
        return "0.0.1"
    parts = [int(n) for n in nums]
    parts[-1] += 1
    return ".".join(str(p) for p in parts)


def dynamic_version() -> str:
    global _computed_version
    if _computed_version:
        return _computed_version

    try:
        project_name = read_project_name()
    except Exception:
        return "0.0.0"

    pypi_version = fetch_latest_version(project_name)
    cached_version = read_internal_cache_version()

    base_version: str | None = None

    if pypi_version and cached_version and HAS_PACKAGING:
        try:
            if Version(pypi_version) >= Version(cached_version):
                base_version = pypi_version
            else:
                base_version = cached_version
        except InvalidVersion:
            base_version = pypi_version
    else:
        base_version = pypi_version or cached_version

    if base_version is not None:
        new_version = calculate_next_version(base_version)
        if ENV_ALLOW_AUTO_INCREMENT:
            write_version_cache(new_version)
            update_about_py(new_version)
            _computed_version = new_version
            return new_version
        return base_version

    if IS_INITIAL_PUBLISH and ENV_ALLOW_AUTO_INCREMENT:
        new_version = "0.0.1"
        write_version_cache(new_version)
        update_about_py(new_version)
        _computed_version = new_version
        return new_version

    return "0.0.0"


setup(version=dynamic_version())
