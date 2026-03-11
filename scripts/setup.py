"""Dynamic version setup for the project."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from setuptools import setup

IS_INITIAL_PUBLISH = True


def read_project_name() -> str:
    with open("pyproject.toml") as f:
        for line in f:
            if line.startswith("name ="):
                return line.split("=")[1].strip().strip("\"")
    raise FileNotFoundError("Project name not found in pyproject.toml")


def read_internal_cache_version() -> str | None:
    try:
        with open(".version_cache") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def fetch_latest_version(project_name: str) -> str | None:
    url = f"https://pypi.org/pypi/{project_name}/json"
    try:
        with urlopen(url) as response:  # nosec B310
            if getattr(response, "status", 200) != 200:
                return None
            data = json.loads(response.read())
            return data.get("info", {}).get("version")
    except (HTTPError, URLError):
        return None


def write_version_cache(version: str) -> None:
    with open(".version_cache", "w") as f:
        f.write(version)


def calculate_next_version(latest_version: str) -> str:
    major, minor, patch = map(int, latest_version.split("."))
    if patch < 20:
        patch += 1
    else:
        patch = 0
        if minor < 10:
            minor += 1
        else:
            minor = 0
            major += 1
    return f"{major}.{minor}.{patch}"


def dynamic_version() -> str:
    project_name = read_project_name()
    latest_version = fetch_latest_version(project_name)

    if latest_version is None:
        cached_version = read_internal_cache_version()
        if cached_version is not None:
            new_version = calculate_next_version(cached_version)
            write_version_cache(new_version)
            return new_version
        if IS_INITIAL_PUBLISH:
            new_version = "0.0.1"
            write_version_cache(new_version)
            return new_version
        raise RuntimeError("Unable to determine version.")

    new_version = calculate_next_version(latest_version)
    write_version_cache(new_version)
    return new_version


setup(version=dynamic_version())
