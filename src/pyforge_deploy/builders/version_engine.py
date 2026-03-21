import json
import os
import re
import sys
import time
from typing import cast
from urllib.error import HTTPError
from urllib.request import urlopen

import toml
from packaging.version import Version

from pyforge_deploy.colors import color_text
from pyforge_deploy.errors import (
    ValidationError,
    VersionError,
)
from pyforge_deploy.logutil import log as logutil


def _log(message: str, color: str = "blue") -> None:
    verbose = os.environ.get("PYFORGE_VERBOSE") == "1" or os.environ.get("CI") == "true"
    if verbose:
        logutil(message, level="debug", color=color, component="version_engine")


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
    del project_name  # preserved for backward-compatible function signature
    canonical_cache = _get_version_cache_path(project_path)
    legacy_cache = _get_legacy_version_cache_path(project_path)
    if os.path.exists(canonical_cache):
        return canonical_cache
    if os.path.exists(legacy_cache):
        return legacy_cache
    return canonical_cache


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
        raise ValidationError("Project name missing in pyproject.toml")
    if isinstance(dynamic, list) and "version" in dynamic:
        return name, "dynamic"
    return name, version or "0.0.0"


_PYPI_CACHE: dict[str, str] = {}
_VERSION_CACHE_DIR = ".pyforge-deploy-cache"
_VERSION_CACHE_FILE = "version_cache"
_LEGACY_VERSION_CACHE_FILE = ".version_cache"


_LEGACY_TO_PRIDE_BUMP: dict[str, str] = {
    "major": "proud",
    "minor": "default",
    "patch": "shame",
}
_SEMVER_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _canonical_bump_type(bump_type: str) -> str:
    """Map legacy bump aliases to Pride bump names."""
    normalized = bump_type.strip().lower()
    return _LEGACY_TO_PRIDE_BUMP.get(normalized, normalized)


def normalize_pride_version(version: str) -> str:
    """Normalize arbitrary version strings to PROUD.DEFAULT.SHAME style.

    The core release segment is always represented as three integers
    (proud.default.shame). PEP 440 pre/dev/post/local labels are preserved.
    """
    v = Version(version)
    release = list(v.release[:3])
    while len(release) < 3:
        release.append(0)

    normalized = f"{release[0]}.{release[1]}.{release[2]}"
    if v.pre is not None:
        normalized += f"{v.pre[0]}{v.pre[1]}"
    if v.post is not None:
        normalized += f".post{v.post}"
    if v.dev is not None:
        normalized += f".dev{v.dev}"
    if v.local is not None:
        normalized += f"+{v.local}"
    return normalized


def _get_network_cache_dir(project_path: str) -> str:
    """Return persistent cache directory path for project."""
    return os.path.join(project_path, ".pyforge-deploy-cache")


def _get_version_cache_path(project_path: str) -> str:
    """Return canonical version cache path within persistent cache directory."""
    return os.path.join(project_path, _VERSION_CACHE_DIR, _VERSION_CACHE_FILE)


def _get_legacy_version_cache_path(project_path: str) -> str:
    """Return legacy root-level version cache path used by older releases."""
    return os.path.join(project_path, _LEGACY_VERSION_CACHE_FILE)


def _get_pypi_cache_file(project_path: str) -> str:
    """Return persistent PyPI cache file path for project."""
    return os.path.join(_get_network_cache_dir(project_path), "pypi_network_cache.json")


def _get_pypi_cache_ttl() -> int:
    """Return PyPI network cache TTL in seconds."""
    try:
        return max(0, int(os.environ.get("PYFORGE_PYPI_CACHE_TTL", "600")))
    except Exception:
        return 600


def _read_pypi_disk_cache(project_path: str) -> dict[str, dict[str, object]]:
    """Read persistent PyPI cache from disk."""
    path = _get_pypi_cache_file(project_path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
            if isinstance(payload, dict):
                return cast(dict[str, dict[str, object]], payload)
    except Exception as e:
        _log(f"Could not read PyPI cache file: {e}", "yellow")
    return {}


def _write_pypi_disk_cache(
    project_path: str, data: dict[str, dict[str, object]]
) -> None:
    """Write persistent PyPI cache to disk."""
    try:
        cache_dir = _get_network_cache_dir(project_path)
        os.makedirs(cache_dir, exist_ok=True)
        path = _get_pypi_cache_file(project_path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except Exception as e:
        _log(f"Could not write PyPI cache file: {e}", "yellow")


def _read_pypi_cached_version(project_name: str, project_path: str) -> str | None:
    """Read cached PyPI version from disk if not expired."""
    cache = _read_pypi_disk_cache(project_path)
    entry = cache.get(project_name)
    if not isinstance(entry, dict):
        return None

    version = entry.get("version")
    fetched_at = entry.get("fetched_at")
    if not isinstance(version, str) or not version:
        return None
    if not isinstance(fetched_at, int | float):
        return None

    ttl = _get_pypi_cache_ttl()
    age = time.time() - float(fetched_at)
    if ttl > 0 and age > ttl:
        return None
    return version


def _read_stale_pypi_cached_version(project_name: str, project_path: str) -> str | None:
    """Read cached PyPI version from disk even if expired."""
    cache = _read_pypi_disk_cache(project_path)
    entry = cache.get(project_name)
    if not isinstance(entry, dict):
        return None
    version = entry.get("version")
    return version if isinstance(version, str) and version else None


def _write_pypi_cached_version(
    project_name: str,
    version: str,
    project_path: str,
) -> None:
    """Persist latest fetched PyPI version to disk cache."""
    cache = _read_pypi_disk_cache(project_path)
    cache[project_name] = {"version": version, "fetched_at": time.time()}
    _write_pypi_disk_cache(project_path, cache)


def fetch_latest_git_version(project_path: str) -> str | None:
    """Fetch latest semantic release version from tags merged into HEAD."""
    import shutil
    import subprocess  # nosec B404

    git_exe = shutil.which("git")
    if not git_exe:
        return None

    try:
        result = subprocess.run(
            [git_exe, "-C", project_path, "tag", "--merged", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )  # nosec B603
        if result.returncode != 0:
            return None

        versions: list[str] = []
        for raw_tag in result.stdout.splitlines():
            tag = raw_tag.strip()
            match = _SEMVER_TAG_RE.match(tag)
            if not match:
                continue
            versions.append(f"{match.group(1)}.{match.group(2)}.{match.group(3)}")

        if not versions:
            return None
        return max(versions, key=Version)
    except Exception as e:
        _log(f"Could not resolve latest git release version: {e}", "yellow")
        return None


def fetch_latest_version(project_name: str, timeout: float = 3.0) -> str | None:
    """Fetches the latest version from PyPI with in-memory caching."""
    global _PYPI_CACHE
    project_path = get_project_path()

    if project_name in _PYPI_CACHE:
        return _PYPI_CACHE[project_name]

    cached_disk_version = _read_pypi_cached_version(project_name, project_path)
    if cached_disk_version:
        _PYPI_CACHE[project_name] = cached_disk_version
        return cached_disk_version

    url = f"https://pypi.org/pypi/{project_name}/json"
    if not url.startswith("https://"):
        return None

    try:
        with urlopen(url, timeout=timeout) as response:  # nosec B310
            if getattr(response, "status", 200) == 200:
                data = json.loads(response.read().decode("utf-8"))
                raw_version = data.get("info", {}).get("version")
                if not isinstance(raw_version, str) or not raw_version.strip():
                    _log(
                        (
                            "PyPI response did not include a valid 'info.version' "
                            f"for project '{project_name}'."
                        ),
                        "yellow",
                    )
                    return None
                version = raw_version.strip()
                _PYPI_CACHE[project_name] = version
                _write_pypi_cached_version(project_name, version, project_path)
                return version
    except HTTPError as e:
        if e.code == 404:
            _log(
                (
                    f"Package '{project_name}' not found on PyPI. "
                    "Assuming initial release."
                ),
                "cyan",
            )
        else:
            _log(f"Failed to fetch PyPI version for {project_name}: {e}", "yellow")
    except Exception as e:
        _log(f"Failed to fetch PyPI version for {project_name}: {e}", "yellow")

    stale = _read_stale_pypi_cached_version(project_name, project_path)
    if stale:
        _PYPI_CACHE[project_name] = stale
        return stale

    return None


def write_version_cache(cache_path: str, version: str) -> None:
    try:
        cache_dir = os.path.dirname(cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(version)
    except Exception as e:
        _log(f"Error writing version cache: {e}", "red")


def get_tool_config() -> dict[str, object]:
    """Reads the [tool.pyforge-deploy] configuration from pyproject.toml."""
    try:
        p_path = get_pyproject_path()
        if os.path.exists(p_path):
            with open(p_path, encoding="utf-8") as f:
                data = toml.load(f)
                return cast(
                    dict[str, object], data.get("tool", {}).get("pyforge-deploy", {})
                )
    except Exception as e:
        _log(f"Could not read tool config from pyproject.toml: {e}", "yellow")
    return {}


def calculate_next_version(current_version: str, bump_type: str = "patch") -> str:
    """
    Calculates the next version given bump type, supporting PEP 440 pre-releases.
    Logs malformed input.
    """
    try:
        v = Version(current_version)
    except Exception as e:
        _log(f"Malformed version string: {current_version}", "red")
        raise VersionError(
            color_text(
                f"Cannot auto-increment malformed version: {current_version}", "red"
            )
        ) from e

    major = v.major
    minor = v.minor
    patch = v.micro
    pre = v.pre
    normalized_bump = _canonical_bump_type(bump_type)

    if normalized_bump == "proud":
        return f"{major + 1}.0.0"

    elif normalized_bump == "default":
        return f"{major}.{minor + 1}.0"

    elif normalized_bump == "shame":
        if pre is not None:
            return f"{major}.{minor}.{patch}"
        return f"{major}.{minor}.{patch + 1}"

    elif normalized_bump in ("alpha", "beta", "rc"):
        phase_map = {"alpha": "a", "beta": "b", "rc": "rc"}
        target_phase = phase_map[normalized_bump]

        if pre is None:
            return f"{major}.{minor}.{patch + 1}{target_phase}1"
        else:
            current_phase, current_num = pre[0], pre[1]
            if current_phase == target_phase:
                return f"{major}.{minor}.{patch}{target_phase}{current_num + 1}"
            else:
                return f"{major}.{minor}.{patch}{target_phase}1"
    else:
        _log(f"Invalid bump_type: {bump_type}", "red")
        raise VersionError(
            color_text(
                (
                    "bump_type must be one of: "
                    "proud/default/shame (or major/minor/patch aliases), "
                    "alpha, beta, rc"
                ),
                "red",
            )
        )


def suggest_bump_from_git(max_commits: int = 32) -> str:
    """Suggest a bump type based on recent git commit messages.

    Uses conventional commit format analysis:
    - 'BREAKING CHANGE:' in body or '!' in header -> proud
    - 'feat' commits -> default
    - 'fix'/'refactor'/'perf' commits -> shame
    - Inspect full commit bodies for footer analysis

    Args:
        max_commits: Maximum number of commits to analyze.

    Returns:
        'proud', 'default', or 'shame' based on commit analysis.
    """
    import shutil
    import subprocess  # nosec B404

    try:
        git_exe = shutil.which("git")
        if not git_exe:
            _log(
                "git executable not found in PATH; cannot suggest bump from git",
                "yellow",
            )
            raise ValidationError(
                "git executable not found in PATH; install Git to use commit analysis"
            )

        tag_proc = subprocess.run(
            [git_exe, "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
        )  # nosec B603

        if tag_proc.returncode == 0 and tag_proc.stdout.strip():
            latest_tag = tag_proc.stdout.strip()
            log_target = f"{latest_tag}..HEAD"
        else:
            log_target = f"-n{max_commits}"

        # Get commits with full body (format: %H%n%s%n%b%n---COMMIT_SEP---%n)
        out = subprocess.run(
            [
                git_exe,
                "log",
                log_target,
                "--pretty=format:%H%n%s%n%b%n---COMMIT_SEP---%n",
            ],
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603

        commits_text = out.stdout
        if not commits_text.strip():
            _log("No git commits found", "yellow")
            return "shame"

        # Split commits by separator
        commit_blocks = commits_text.split("---COMMIT_SEP---")
        has_breaking = False
        has_feature = False
        has_fix = False

        for block in commit_blocks:
            if not block.strip():
                continue

            lines = block.strip().split("\n", 1)
            if not lines:
                continue

            header = lines[0]  # First line is the commit message
            body = lines[1] if len(lines) > 1 else ""

            # Check for breaking changes in header (with ! indicator)
            if "!" in header or "BREAKING CHANGE" in header:
                has_breaking = True
                break

            # Check for breaking changes in body (BREAKING CHANGE: footer)
            if "BREAKING CHANGE:" in body:
                has_breaking = True
                break

            # Extract commit type (feat:, fix:, refactor:, perf:, etc.)
            commit_type = ""
            if ":" in header:
                commit_type = header.split(":")[0].split("(")[0].lower().strip()

            if commit_type == "feat":
                has_feature = True
            elif commit_type in ("fix", "refactor", "perf", "performance"):
                has_fix = True

        # Decide bump based on findings
        if has_breaking:
            return "proud"
        elif has_feature:
            return "default"
        elif has_fix:
            return "shame"

        return "shame"

    except subprocess.CalledProcessError as e:
        _log(f"Git inspection failed: {e.stderr or str(e)}", "yellow")
        return "shame"
    except FileNotFoundError:
        _log("git executable not found", "yellow")
        return "shame"
    except Exception as e:
        _log(f"Unexpected error while inspecting git commits: {e}", "yellow")
        return "shame"


def read_local_version(cache_path: str) -> str | None:
    """
    Reads the local version from cache or about file. Logs malformed content.
    """
    if not os.path.exists(cache_path):
        _log(f"Cache file not found: {cache_path}", "yellow")
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        _log(f"Error reading cache file: {e}", "red")
        return None
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if match:
        return match.group(1)
    if content and content[0].isdigit():
        return content
    _log(f"Malformed cache content: {content}", "yellow")
    return None


def write_both_caches(
    project_path: str, project_name: str, version: str, dry_run: bool = False
) -> None:
    del project_name  # preserved for backward-compatible function signature
    if dry_run:
        print(
            color_text(
                (f"  [DRY RUN] Would write version '{version}' to cache file."),
                "yellow",
            )
        )
        return

    def safe_write(path: str, content: str) -> None:
        """Atomically write content to a file."""
        tmp_path = f"{path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            print(color_text(f"Error: Writing {path} failed: {e}", "red"))

    cache_path = _get_version_cache_path(project_path)
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    except Exception as e:
        print(
            color_text(
                f"Warning: Could not create directories for {cache_path}: {e}",
                "yellow",
            )
        )
    safe_write(cache_path, version)

    legacy_cache = _get_legacy_version_cache_path(project_path)
    if os.path.exists(legacy_cache):
        try:
            os.remove(legacy_cache)
        except Exception as e:
            _log(
                f"Warning: Could not remove legacy cache file {legacy_cache}: {e}",
                "yellow",
            )


def get_dynamic_version(
    MANUAL_VERSION: str | None = None,
    BUMP_TYPE: str | None = None,
    AUTO_INCREMENT: bool = False,
    DRY_RUN: bool = False,
    WRITE_CACHE: bool = True,
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

    root = find_project_root(os.getcwd())

    explicit_project_version: str | None = None
    if project_version != "dynamic" and MANUAL_VERSION is None:
        if not AUTO_INCREMENT and not BUMP_TYPE:
            try:
                normalized_project_version = normalize_pride_version(project_version)
                if WRITE_CACHE and normalized_project_version != project_version:
                    write_both_caches(
                        root,
                        project_name,
                        normalized_project_version,
                        dry_run=DRY_RUN,
                    )
                return normalized_project_version
            except Exception:
                return project_version
        try:
            explicit_project_version = normalize_pride_version(project_version)
        except Exception:
            explicit_project_version = project_version

    if MANUAL_VERSION is not None:
        manual_version = MANUAL_VERSION
        try:
            manual_version = normalize_pride_version(MANUAL_VERSION)
        except Exception as exc:
            _log(
                f"Could not normalize manual version '{MANUAL_VERSION}': {exc}",
                "yellow",
            )
        if WRITE_CACHE:
            write_both_caches(root, project_name, manual_version, dry_run=DRY_RUN)
        return manual_version

    # Gather candidate sources for cached versions
    candidates = [
        _get_version_cache_path(root),
        _get_legacy_version_cache_path(root),
    ]
    cached_version = None
    for candidate in candidates:
        cached_version = read_local_version(candidate)
        if cached_version:
            try:
                cached_version = normalize_pride_version(cached_version)
            except Exception as exc:
                _log(
                    f"Could not normalize cached version from {candidate}: {exc}",
                    "yellow",
                )
            break

    pypi_version = fetch_latest_version(project_name)
    if pypi_version:
        try:
            pypi_version = normalize_pride_version(pypi_version)
        except Exception as exc:
            _log(
                f"Could not normalize PyPI version '{pypi_version}': {exc}",
                "yellow",
            )

    git_version = fetch_latest_git_version(root)
    if git_version:
        try:
            git_version = normalize_pride_version(git_version)
        except Exception as exc:
            _log(
                f"Could not normalize git tag version '{git_version}': {exc}",
                "yellow",
            )

    base_version = explicit_project_version or "0.0.0"
    try:
        candidate_versions = [
            version
            for version in [
                pypi_version,
                cached_version,
                explicit_project_version,
                git_version,
            ]
            if version
        ]
        if candidate_versions:
            base_version = max(candidate_versions, key=Version)
    except Exception as e:
        print(color_text(f"Version comparison error: {e}", "yellow"))
        base_version = (
            pypi_version
            or git_version
            or cached_version
            or explicit_project_version
            or "0.0.0"
        )

    next_version = calculate_next_version(base_version, BUMP_TYPE or "shame")
    stable_bumps = {"proud", "default", "shame", "major", "minor", "patch"}
    if AUTO_INCREMENT or (BUMP_TYPE and BUMP_TYPE in stable_bumps):
        if WRITE_CACHE:
            write_both_caches(root, project_name, next_version, dry_run=DRY_RUN)
        return next_version
    return base_version


# Expose module under test-friendly alias used by tests
sys.modules.setdefault(
    "src.pyforge_deploy.builders.version_engine", sys.modules[__name__]
)
