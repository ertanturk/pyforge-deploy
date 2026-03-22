"""CLI module for pyforge_deploy."""

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from pyforge_deploy.builders.changelog_engine import (
    ChangelogEngine,
    run_release_intelligence,
)
from pyforge_deploy.builders.docker import DockerBuilder
from pyforge_deploy.builders.docker_engine import detect_dependencies
from pyforge_deploy.builders.entry_point_detector import (
    detect_entry_point,
    list_potential_entry_points,
)
from pyforge_deploy.builders.parallel import batch_execute_functions
from pyforge_deploy.builders.pypi import PyPIDistributor
from pyforge_deploy.builders.version_engine import (
    fetch_latest_version,
    get_dynamic_version,
    get_project_details,
)
from pyforge_deploy.colors import color_text
from pyforge_deploy.config import resolve_setting
from pyforge_deploy.errors import PyForgeError
from pyforge_deploy.logutil import log as logutil
from pyforge_deploy.plugin_engine import run_hooks
from pyforge_deploy.release.service import ReleaseService
from pyforge_deploy.templates.workflows import GITHUB_RELEASE_YAML


class HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter
):
    """CLI help formatter with preserved layout and default values."""


def _log(message: str, color: str = "blue", verbose: bool = False) -> None:
    from pyforge_deploy.colors import is_ci_environment

    if verbose or is_ci_environment():
        logutil(message, level="debug", color=color, component="CLI")


def _warn_deprecated_command() -> None:
    """Print a deprecation warning for non-primary commands."""
    print(
        color_text(
            "This command is deprecated. Use `pyforge release` instead.",
            "yellow",
        )
    )


EXAMPLES = f"""
{color_text("Quick Start", "magenta", bold=True)}
    {color_text("Primary", "blue", bold=True)}
        pyforge release                                  {color_text("# Analyze commits -> suggest version -> generate changelog", "gray", bold=False)}
        pyforge release --dry-run                        {color_text("# Preview full release output safely", "gray", bold=False)}

    {color_text("Advanced (Deprecated)", "blue", bold=True)}
        pyforge-deploy deploy-pypi                       {color_text("# Legacy publish command", "gray", bold=False)}
        pyforge-deploy docker-build --push               {color_text("# Legacy container workflow", "gray", bold=False)}
"""  # noqa: E501

OVERVIEW = f"""
{color_text("From messy commits to clean releases in one command.", "cyan")}

{color_text("Command Center", "magenta", bold=True)}
    {color_text("release", "green")}          Primary workflow (recommended)
    {color_text("init", "yellow")}            Deprecated advanced command
    {color_text("deploy-pypi", "yellow")}     Deprecated advanced command
    {color_text("docker-build", "yellow")}    Deprecated advanced command
    {color_text("show-* / status", "yellow")} Deprecated advanced commands

{color_text("Typical workflow:", "blue")}
    1) pyforge release
    2) Confirm suggested version and changelog
    3) Let CI publish (or use --local-publish)

{color_text("Tip:", "yellow")} Use a subcommand with -h for focused help.
    pyforge-deploy docker-build -h
"""

DOCKER_EXAMPLES = f"""
{color_text("Examples:", "magenta")}
    pyforge-deploy docker-build
    pyforge-deploy docker-build --image-tag user/app:1.2.3
    pyforge-deploy docker-build --platforms linux/amd64,linux/arm64 --push
    pyforge-deploy docker-build --dry-run --verbose
"""

PYPI_EXAMPLES = f"""
{color_text("Examples:", "magenta")}
    pyforge-deploy deploy-pypi
    pyforge-deploy deploy-pypi --bump default
    pyforge-deploy deploy-pypi --version 1.2.0 --test
    pyforge-deploy deploy-pypi --dry-run --verbose
"""


def get_banner() -> str:
    line = color_text("━" * 60, "magenta")
    title = color_text("PYFORGE DEPLOY", "magenta", bold=True).center(70)
    return f"\n{line}\n{title}\n{line}"


def _get_last_release_tag() -> str:
    """Return latest git tag, if available."""
    git_exe = shutil.which("git")
    if not git_exe:
        return "Unavailable (git not found)"

    try:
        result = subprocess.run(
            [git_exe, "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )  # nosec B603
        if result.returncode == 0:
            tag = result.stdout.strip()
            return tag or "None"
        return "None"
    except Exception:
        return "Unavailable"


def _get_github_repo_slug() -> str | None:
    """Return GitHub repo slug in owner/repo format from origin URL."""
    git_exe = shutil.which("git")
    if not git_exe:
        return None

    try:
        result = subprocess.run(
            [git_exe, "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )  # nosec B603
        if result.returncode != 0:
            return None

        origin = result.stdout.strip()
        if not origin:
            return None

        # git@github.com:owner/repo.git
        if origin.startswith("git@github.com:"):
            slug = origin.split("git@github.com:", 1)[1]
        # https://github.com/owner/repo(.git)
        elif "github.com/" in origin:
            slug = origin.split("github.com/", 1)[1]
        else:
            return None

        if slug.endswith(".git"):
            slug = slug[:-4]

        parts = slug.split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}/{parts[1]}"
        return None
    except Exception:
        return None


def _get_last_release_published_at(tag: str) -> str:
    """Return latest release published time for a tag.

    Tries GitHub Releases API first, then falls back to local git tag commit date.
    """

    def _format_datetime_human(raw_value: str) -> str:
        """Convert ISO date-time into a human-readable UTC format."""
        normalized = raw_value.strip().replace("Z", "+00:00")
        if not normalized:
            return "Unavailable"

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return raw_value

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.astimezone(UTC)
        return parsed.strftime("%b %d, %Y %H:%M UTC")

    if tag in {"None", "Unavailable", "Unavailable (git not found)"}:
        return "N/A"

    repo_slug = _get_github_repo_slug()
    if repo_slug:
        api_url = f"https://api.github.com/repos/{repo_slug}/releases/tags/{tag}"
        try:
            with urlopen(api_url, timeout=5) as response:  # nosec B310
                if getattr(response, "status", 200) == 200:
                    payload = json.loads(response.read().decode("utf-8"))
                    published = payload.get("published_at")
                    if isinstance(published, str) and published:
                        return _format_datetime_human(published)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            _log(f"GitHub release date lookup failed: {e}", "yellow")
        except Exception as e:
            _log(f"Unexpected release API error: {e}", "yellow")

    git_exe = shutil.which("git")
    if not git_exe:
        return "Unavailable"

    try:
        result = subprocess.run(
            [git_exe, "log", "-1", "--format=%cI", tag],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )  # nosec B603
        if result.returncode == 0:
            date = result.stdout.strip()
            return _format_datetime_human(date)
        return "Unavailable"
    except Exception:
        return "Unavailable"


def _check_docker_image_status(image_tag: str | None) -> str:
    """Check whether configured Docker image/tag exists on Docker Hub.

    This check supports Docker Hub style tags (`namespace/repo:tag` and
    `repo:tag`). For other registries, returns an informative status.
    """
    if not image_tag:
        return "Not configured"

    image = image_tag.strip()
    if not image:
        return "Not configured"

    # Non Docker Hub registry (e.g. ghcr.io/org/repo:tag)
    first_segment = image.split("/", 1)[0]
    if "." in first_segment or ":" in first_segment:
        return "Skipped (non-Docker Hub registry)"

    # Normalize to Docker Hub API path
    repo_with_tag = image
    if "/" not in repo_with_tag:
        repo_with_tag = f"library/{repo_with_tag}"
    if ":" in repo_with_tag:
        repo, tag = repo_with_tag.rsplit(":", 1)
    else:
        repo, tag = repo_with_tag, "latest"

    api_url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"
    try:
        with urlopen(api_url, timeout=5) as response:  # nosec B310
            if getattr(response, "status", 200) == 200:
                return "Exists"
            return "Unknown"
    except HTTPError as e:
        if e.code == 404:
            return "Not found"
        return f"Unavailable (HTTP {e.code})"
    except URLError:
        return "Unavailable (network)"
    except Exception:
        return "Unavailable"


def _extract_changelog_section_for_version(changelog_path: Path, version: str) -> str:
    """Extract changelog section for a version, supporting v/non-v headings."""
    if not changelog_path.exists():
        return f"Release v{version}\n\nNo CHANGELOG.md found."

    lines = changelog_path.read_text(encoding="utf-8").splitlines()
    plain = version.lstrip("v")
    starts = [
        f"## [v{plain}]",
        f"## [{plain}]",
        f"## [v{version}]",
        f"## [{version}]",
    ]

    start_idx = -1
    for i, line in enumerate(lines):
        if any(line.startswith(prefix) for prefix in starts):
            start_idx = i
            break

    if start_idx == -1:
        return f"Release v{plain}\n\nNo matching changelog section found."

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break
    return "\n".join(lines[start_idx:end_idx]).strip()


def _publish_github_release(
    version: str, changelog_path: Path, *, verbose: bool
) -> None:
    """Publish GitHub Release using changelog section as body.

    Requires ``GITHUB_TOKEN`` or ``GH_TOKEN`` and a GitHub origin remote.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo_slug = _get_github_repo_slug()
    plain = version.lstrip("v")
    tag = f"v{plain}"

    if not token:
        _log(
            "Skipping GitHub release publish: GITHUB_TOKEN/GH_TOKEN not set.",
            "yellow",
            verbose,
        )
        return
    if not repo_slug:
        _log(
            "Skipping GitHub release publish: could not resolve GitHub repository.",
            "yellow",
            verbose,
        )
        return

    release_body = _extract_changelog_section_for_version(changelog_path, plain)
    payload = {
        "tag_name": tag,
        "name": plain,
        "body": release_body,
        "draft": False,
        "prerelease": False,
    }
    req = Request(
        f"https://api.github.com/repos/{repo_slug}/releases",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as response:  # nosec B310
            if getattr(response, "status", 201) in {200, 201}:
                _log(f"Published GitHub release for tag {tag}.", "green", verbose)
                return
    except HTTPError as e:
        if e.code == 422:
            _log(
                (
                    f"GitHub release for {tag} already exists (HTTP 422). "
                    "Skipping publish."
                ),
                "yellow",
                verbose,
            )
            return
        raise


def _finalize_release_git_ops(
    version: str,
    *,
    project_root: str,
    allow_dirty: bool,
    verbose: bool,
) -> None:
    """Finalize release by committing changelog, tagging and pushing."""
    engine = ChangelogEngine(project_root=project_root, verbose=verbose)
    engine.finalize_release_git_ops(version, allow_dirty=allow_dirty)


def main() -> None:
    load_dotenv()
    verbose = "--verbose" in sys.argv
    _log("Starting CLI main()", "magenta", verbose)
    parser = argparse.ArgumentParser(
        prog="pyforge",
        description=f"{get_banner()}\n{OVERVIEW}",
        epilog=EXAMPLES,
        formatter_class=HelpFormatter,
        add_help=False,
    )
    _log("Argument parser initialized", "cyan", verbose)

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {color_text(get_dynamic_version(), 'green')}",
    )
    _log("Added --version argument", "cyan", verbose)

    global_group = parser.add_argument_group(color_text("Global Options", "blue"))
    global_group.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit."
    )
    global_group.add_argument(
        "--verbose",
        action="store_true",
        default=None,
        help="Detailed debug logging.",
    )
    global_group.add_argument(
        "-y",
        "--yes",
        action="store_true",
        default=None,
        help="Non-interactive mode (Auto-confirm).",
    )
    _log("Added global arguments", "cyan", verbose)

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        title=color_text("Commands", "blue"),
        description=(
            f"{color_text('Release & Build', 'magenta')} : init, deploy-pypi, "
            "docker-build\n"
            f"{color_text('Discovery', 'magenta')}       : show-deps, "
            "show-entry-point, show-version\n"
            f"{color_text('Health', 'magenta')}          : status"
        ),
        metavar="COMMAND",
        help="Run 'pyforge-deploy <command> -h' for command-specific options.",
    )
    _log("Subparsers for commands added", "cyan", verbose)

    init_parser = subparsers.add_parser(
        "init",
        help="Bootstrap workflow and versioning files.",
        description=(
            "Initialize project automation and starter release assets:\n"
            "- .github/workflows/pyforge-deploy.yml\n"
            "- .dockerignore + .env.example\n"
            "- .pyforge-deploy-cache\n"
            "- version cache (.pyforge-deploy-cache/version_cache) when absent"
        ),
        epilog=(
            f"{color_text('After init:', 'yellow')} "
            "run `pyforge-deploy status` to verify release readiness."
        ),
        formatter_class=HelpFormatter,
    )

    docker_parser = subparsers.add_parser(
        "docker-build",
        help="Build and optionally push Docker images.",
        aliases=["docker", "build-docker"],
        description=(
            "Automatically scans project for dependencies, renders a Dockerfile, "
            "and builds an image."
        ),
        epilog=DOCKER_EXAMPLES,
        formatter_class=HelpFormatter,
    )
    docker_build_inputs = docker_parser.add_argument_group(
        color_text("Build Inputs", "blue")
    )
    docker_build_inputs.add_argument("--entry-point", type=str, default=None)
    docker_build_inputs.add_argument("--image-tag", type=str, default=None)
    docker_build_inputs.add_argument(
        "--platforms",
        type=str,
        default=None,
        help=(
            "Comma-separated platforms (e.g., linux/amd64,linux/arm64) "
            "for multi-arch builds."
        ),
    )

    docker_execution_mode = docker_parser.add_argument_group(
        color_text("Execution Mode", "blue")
    )
    docker_execution_mode.add_argument(
        "--verbose", action="store_true", default=None, help="Enable verbose logging."
    )
    docker_execution_mode.add_argument(
        "--push",
        action="store_true",
        default=None,
        help="Push the generated image to Docker Hub/Registry.",
    )
    docker_execution_mode.add_argument(
        "-y",
        "--yes",
        action="store_true",
        default=None,
        help="Automatically say yes to prompts.",
    )
    docker_execution_mode.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Simulate the process without making changes.",
    )

    def init_handler(args: argparse.Namespace) -> None:
        _warn_deprecated_command()
        workflow_dir = Path(".github/workflows")
        workflow_dir.mkdir(parents=True, exist_ok=True)
        target_path = workflow_dir / "pyforge-deploy.yml"

        def _next_backup_path(path: Path) -> Path:
            """Return first available backup path (e.g. file.bak, file.bak.1)."""
            candidate = path.with_suffix(f"{path.suffix}.bak")
            if not candidate.exists():
                return candidate
            idx = 1
            while True:
                rotated = Path(f"{candidate}.{idx}")
                if not rotated.exists():
                    return rotated
                idx += 1

        def _upsert_env_example(path: Path) -> tuple[str, str]:
            """Create/merge .env.example with common deployment variables."""
            defaults: list[str] = [
                "# PyForge Deploy local environment example",
                "PYPI_TOKEN=",
                "DOCKERHUB_USERNAME=",
                "DOCKERHUB_TOKEN=",
                "PYFORGE_VERBOSE=1",
                "PYFORGE_JSON_LOGS=0",
            ]
            if not path.exists():
                path.write_text("\n".join(defaults) + "\n", encoding="utf-8")
                return f"Created: {path}", "green"

            existing = path.read_text(encoding="utf-8").splitlines()
            existing_keys = {
                line.split("=", 1)[0].strip()
                for line in existing
                if line and not line.startswith("#") and "=" in line
            }
            to_add: list[str] = []
            for line in defaults:
                if line.startswith("#"):
                    continue
                key = line.split("=", 1)[0]
                if key not in existing_keys:
                    to_add.append(line)

            if not to_add:
                return f"{path} already has required keys.", "blue"

            with path.open("a", encoding="utf-8") as f:
                f.write("\n# Added by pyforge-deploy init\n")
                for line in to_add:
                    f.write(f"{line}\n")
            return f"Updated {path} with {len(to_add)} missing keys.", "green"

        def _ensure_workflow() -> list[tuple[str, str]]:
            """Create/update workflow file, preserving backups when needed."""
            messages: list[tuple[str, str]] = []
            desired_workflow = GITHUB_RELEASE_YAML.strip() + "\n"
            if target_path.exists():
                current_workflow = target_path.read_text(encoding="utf-8")
                if current_workflow.strip() == desired_workflow.strip():
                    messages.append(
                        (f"Workflow is already up-to-date: {target_path}", "blue")
                    )
                else:
                    backup_path = _next_backup_path(target_path)
                    backup_path.write_text(current_workflow, encoding="utf-8")
                    target_path.write_text(desired_workflow, encoding="utf-8")
                    messages.append((f"Updated workflow: {target_path}", "green"))
                    messages.append((f"Backup created: {backup_path}", "yellow"))
            else:
                target_path.write_text(desired_workflow, encoding="utf-8")
                messages.append((f"Created: {target_path}", "green"))
            return messages

        def _ensure_dockerignore() -> tuple[str, str]:
            """Create/update .dockerignore with critical entries."""
            dockerignore_path = Path(".dockerignore")
            critical_ignores = [
                ".git",
                ".venv",
                "venv",
                "env",
                "__pycache__/",
                "*.pyc",
                "*.pyo",
                "*.pyd",
                ".pytest_cache/",
                ".tox/",
                "build/",
                "dist/",
                "*.egg-info/",
                ".env",
                "tests/",
            ]

            def _normalize_ignore_pattern(pattern: str) -> str:
                """Normalize ignore pattern for semantic comparison.

                Treats directory forms like ``build`` and ``build/`` as equal
                so init does not append duplicate entries.
                """
                return pattern.strip().rstrip("/")

            if not dockerignore_path.exists():
                dockerignore_path.write_text(
                    "\n".join(critical_ignores) + "\n", encoding="utf-8"
                )
                return f"Created: {dockerignore_path}", "green"

            existing_lines = dockerignore_path.read_text(encoding="utf-8").splitlines()
            existing_normalized = {
                _normalize_ignore_pattern(ln)
                for ln in existing_lines
                if ln.strip() and not ln.strip().startswith("#")
            }
            missing = [
                item
                for item in critical_ignores
                if _normalize_ignore_pattern(item) not in existing_normalized
            ]
            if missing:
                with dockerignore_path.open("a", encoding="utf-8") as f:
                    f.write("\n# Added by pyforge-deploy\n")
                    for item in missing:
                        f.write(f"{item}\n")
                return (
                    f"Updated {dockerignore_path} with {len(missing)} entries.",
                    "green",
                )
            return f"{dockerignore_path} already looks good.", "blue"

        def _ensure_cache_dir() -> tuple[str, str]:
            """Create persistent cache directory for pyforge-deploy."""
            cache_dir = Path(".pyforge-deploy-cache")
            if cache_dir.exists():
                return f"{cache_dir} already exists.", "blue"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return f"Created: {cache_dir}", "green"

        try:
            bootstrap_jobs: list[
                tuple[Callable[..., object], tuple[object, ...], dict[str, object]]
            ] = [
                (_ensure_workflow, (), {}),
                (_ensure_dockerignore, (), {}),
                (_upsert_env_example, (Path(".env.example"),), {}),
                (_ensure_cache_dir, (), {}),
            ]
            bootstrap_results = batch_execute_functions(bootstrap_jobs, max_workers=4)
            workflow_messages = cast(
                list[tuple[str, str]], bootstrap_results.get(0, [])
            )
            for message, color in workflow_messages:
                print(color_text(message, color))

            dockerignore_message = cast(
                tuple[str, str],
                bootstrap_results.get(
                    1, ("Skipped .dockerignore update due to internal error.", "yellow")
                ),
            )
            print(color_text(dockerignore_message[0], dockerignore_message[1]))

            env_message = cast(
                tuple[str, str],
                bootstrap_results.get(
                    2, ("Skipped .env.example update due to internal error.", "yellow")
                ),
            )
            print(color_text(env_message[0], env_message[1]))

            cache_message = cast(
                tuple[str, str],
                bootstrap_results.get(
                    3,
                    (
                        "Skipped .pyforge-deploy-cache setup due to internal error.",
                        "yellow",
                    ),
                ),
            )
            print(color_text(cache_message[0], cache_message[1]))

            print(color_text("\nChecking project structure for versioning...", "blue"))
            try:
                _, p_version = get_project_details()
                initial_version = p_version if p_version != "dynamic" else "0.0.0"

                base_dir = Path.cwd()
                cache_file = base_dir / ".pyforge-deploy-cache" / "version_cache"
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                if not cache_file.exists():
                    cache_file.write_text(initial_version, encoding="utf-8")
                    print(
                        color_text(f"Created missing cache file: {cache_file}", "green")
                    )
                else:
                    print(color_text(f"{cache_file} already exists.", "blue"))

            except Exception as e:
                print(
                    color_text(
                        (
                            f"Could not auto-heal version files "
                            f"(is pyproject.toml missing?): {e}"
                        ),
                        "yellow",
                    )
                )

            discovery_jobs: list[
                tuple[Callable[..., object], tuple[object, ...], dict[str, object]]
            ] = [
                (detect_entry_point, (os.getcwd(),), {}),
                (list_potential_entry_points, (os.getcwd(),), {}),
            ]
            discovery_results = batch_execute_functions(discovery_jobs, max_workers=2)
            detected_entry = cast(str | None, discovery_results.get(0))
            candidates = cast(list[str], discovery_results.get(1, []))
            dep_report = detect_dependencies(os.getcwd())

            req_files = cast(list[str], dep_report.get("requirement_files", []))

            print(color_text("\nProject Discovery:", "blue"))
            print(
                color_text(
                    f"- Entry point: {detected_entry or 'Not detected'}",
                    "cyan",
                )
            )
            print(
                color_text(
                    f"- Entry point candidates: {len(candidates)}",
                    "cyan",
                )
            )
            print(
                color_text(
                    (
                        "- Requirement files: "
                        f"{', '.join(req_files) if req_files else 'None'}"
                    ),
                    "cyan",
                )
            )

            print(color_text("\nNext Steps:", "blue"))
            print(
                color_text("1. PyPI Trusted Publishing (Passwordless OIDC):", "yellow")
            )
            print(
                color_text("   - Go to pypi.org -> Your Account -> Publishing.", "cyan")
            )
            print(
                color_text(
                    "   - Add a new 'Trusted Publisher' for this GitHub repository.",
                    "cyan",
                )
            )
            print(
                color_text(
                    "   - (No need to create or store a PYPI_TOKEN anymore!)", "green"
                )
            )
            print(color_text("\n2. Docker Hub (If using Docker):", "yellow"))
            print(
                color_text(
                    "   - Go to your GitHub Repository Settings > Secrets > Actions.",
                    "cyan",
                )
            )
            print(
                color_text(
                    "   - Add 'DOCKERHUB_USERNAME' and 'DOCKERHUB_TOKEN'.", "cyan"
                )
            )
            print(
                color_text(
                    "\n3. Push your changes and watch the magic happen!", "magenta"
                )
            )

        except Exception as e:
            print(color_text(f"Error: Could not complete initialization: {e}", "red"))

    def docker_build_handler(args: argparse.Namespace) -> None:
        _warn_deprecated_command()
        # Config-first resolution: CLI -> pyproject.toml -> env -> defaults

        # Resolve flags and values using resolve_setting so precedence is:
        # CLI -> [tool.pyforge-deploy] -> env -> default
        def _truthy(val: object) -> bool:
            if isinstance(val, bool):
                return val
            if val is None:
                return False
            if isinstance(val, str):
                return val.lower() in ("1", "true", "yes", "y")
            return bool(val)

        do_push = _truthy(
            resolve_setting(
                args.push, "docker_push", env_keys=("DOCKER_PUSH",), default=False
            )
        )
        do_confirm = _truthy(
            resolve_setting(
                args.yes,
                "auto_confirm",
                env_keys=("AUTO_CONFIRM", "PYFORGE_AUTO_CONFIRM"),
                default=False,
            )
        )
        platforms = resolve_setting(
            args.platforms,
            "docker_platforms",
            env_keys=("DOCKER_PLATFORMS",),
            default=None,
        )
        if platforms is not None and not isinstance(platforms, str):
            platforms = str(platforms)

        image_tag = resolve_setting(
            args.image_tag, "docker_image", env_keys=("DOCKER_IMAGE",), default=None
        )

        dry_run = _truthy(
            resolve_setting(
                args.dry_run,
                "docker_dry_run",
                env_keys=("DOCKER_DRY_RUN",),
                default=False,
            )
        )
        verbose_flag = _truthy(resolve_setting(args.verbose, "verbose", default=False))

        # Maintain backward-compatible constructor call (tests expect only
        # entry_point and image_tag). Set additional flags on the instance.
        # Data Flow Explanation:
        # 1. CLI/config resolves flags and image metadata
        # 2. Plugin hook stage `before_build` runs best-effort user commands
        # 3. DockerBuilder renders/builds/pushes image
        # 4. Plugin hook stage `after_build` runs post-build commands
        run_hooks("before_build", verbose=verbose_flag)
        builder = DockerBuilder(entry_point=args.entry_point, image_tag=image_tag)
        # apply resolved flags
        try:
            builder.verbose = verbose_flag
            builder.auto_confirm = do_confirm
            builder.dry_run = dry_run
            builder.platforms = platforms
        except AttributeError as e:
            # Best-effort: if builder doesn't accept these attrs, log and continue
            _log(f"Could not set builder attribute: {e}", "yellow", verbose)
        try:
            builder.deploy(push=bool(do_push))
            run_hooks("after_build", verbose=verbose_flag)
        except Exception as e:
            if os.environ.get("PYFORGE_DEBUG"):
                raise
            print(color_text(f"Error: Docker build failed: {e}", "red"))
            sys.exit(1)

    docker_parser.set_defaults(func=docker_build_handler)
    init_parser.set_defaults(func=init_handler)

    pypi_parser = subparsers.add_parser(
        "deploy-pypi",
        help="Build and publish package to PyPI/TestPyPI.",
        aliases=["deploy", "pypi", "publish"],
        description=(
            "Calculates next version (PEP 440), builds wheel/sdist, "
            "and uploads using uv/twine."
        ),
        epilog=PYPI_EXAMPLES,
        formatter_class=HelpFormatter,
    )
    pypi_release_target = pypi_parser.add_argument_group(
        color_text("Release Target", "blue")
    )
    pypi_release_target.add_argument("--test", action="store_true")
    pypi_release_target.add_argument("--version", type=str, default=None)
    pypi_release_target.add_argument(
        "--release",
        "--release-intel",
        action="store_true",
        default=None,
        dest="release",
        help=(
            "Run deterministic changelog + tagging automation after successful publish."
        ),
    )
    pypi_release_target.add_argument(
        "--bump",
        choices=[
            "proud",
            "default",
            "shame",
            "major",
            "minor",
            "patch",
            "alpha",
            "beta",
            "rc",
        ],
        default=None,
        help=(
            "Version bump type. Supports Pride stable bumps "
            "(proud, default, shame), legacy aliases (major, minor, patch), "
            "and pre-releases (alpha, beta, rc)."
        ),
    )

    pypi_execution_mode = pypi_parser.add_argument_group(
        color_text("Execution Mode", "blue")
    )
    pypi_execution_mode.add_argument(
        "--verbose", action="store_true", default=None, help="Enable verbose logging."
    )
    pypi_execution_mode.add_argument(
        "-y", "--yes", action="store_true", default=None, help="Non-interactive mode."
    )
    pypi_execution_mode.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Simulate deployment without uploading or changing files.",
    )

    def deploy_pypi_handler(args: argparse.Namespace) -> None:
        _warn_deprecated_command()
        bump_arg = args.bump
        if not bump_arg:
            try:
                from pyforge_deploy.builders.version_engine import suggest_bump_from_git

                bump_type = suggest_bump_from_git()
                _log(
                    f"Auto-detected bump type from Git history: {bump_type}", "magenta"
                )
            except Exception:
                bump_type = resolve_setting(None, "default_bump", default="shame")
        else:
            bump_type = bump_arg

        # Resolve common flags via config-first
        def _truthy(val: object) -> bool:
            if isinstance(val, bool):
                return val
            if val is None:
                return False
            if isinstance(val, str):
                return val.lower() in ("1", "true", "yes", "y")
            return bool(val)

        do_confirm = _truthy(
            resolve_setting(
                args.yes,
                "auto_confirm",
                env_keys=("AUTO_CONFIRM", "PYFORGE_AUTO_CONFIRM"),
                default=False,
            )
        )
        dry_run = _truthy(
            resolve_setting(
                args.dry_run, "pypi_dry_run", env_keys=("PYPI_DRY_RUN",), default=False
            )
        )
        verbose_flag = _truthy(resolve_setting(args.verbose, "verbose", default=False))
        enable_release = _truthy(
            resolve_setting(
                args.release,
                "release",
                env_keys=("PYFORGE_RELEASE", "PYFORGE_RELEASE_INTEL"),
                default=False,
            )
        )
        allow_dirty_release = _truthy(
            resolve_setting(
                None,
                "release_allow_dirty",
                env_keys=("PYFORGE_RELEASE_ALLOW_DIRTY",),
                default=False,
            )
        )

        # Keep constructor call minimal for test compatibility
        # Data Flow Explanation:
        # 1. CLI/config resolves release options and bump strategy
        # 2. Plugin hook stage `before_release` runs best-effort commands
        # 3. PyPIDistributor performs build + upload flow
        # 4. Plugin hook stage `after_release` runs post-release commands
        run_hooks("before_release", verbose=verbose_flag)
        distributor = PyPIDistributor(
            target_version=args.version, use_test_pypi=args.test, bump_type=bump_type
        )
        # apply resolved flags to instance
        try:
            distributor.verbose = verbose_flag
            distributor.auto_confirm = do_confirm
            distributor.dry_run = dry_run
        except AttributeError as e:
            _log(f"Could not set distributor attribute: {e}", "yellow", verbose_flag)
        try:
            distributor.deploy()
            if enable_release:
                run_release_intelligence(
                    project_root=os.getcwd(),
                    dry_run=dry_run,
                    target_version=args.version,
                    verbose=verbose_flag,
                    allow_dirty=allow_dirty_release,
                )
            run_hooks("after_release", verbose=verbose_flag)
        except Exception as e:
            if os.environ.get("PYFORGE_DEBUG"):
                raise
            print(color_text(f"PyPI deployment failed: {e}", "red"))
            sys.exit(1)

    pypi_parser.set_defaults(func=deploy_pypi_handler)

    release_parser = subparsers.add_parser(
        "release",
        aliases=["release-intel"],
        help="Analyze commits and ship a clean release in one command.",
        description=(
            "From messy commits to clean releases in one command. "
            "Primary workflow: commit analysis, version suggestion, changelog "
            "generation, confirmation, and release finalization."
        ),
        formatter_class=HelpFormatter,
    )
    release_parser.add_argument(
        "--version",
        type=str,
        default=None,
        help="Optional explicit target release version (e.g. 1.3.0).",
    )
    release_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Preview the full release plan without changing files or git refs.",
    )
    release_parser.add_argument(
        "--verbose",
        action="store_true",
        default=None,
        help="Enable verbose logging.",
    )
    release_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        default=None,
        help="Skip interactive confirmation and apply release plan immediately.",
    )
    release_parser.add_argument(
        "--local-publish",
        action="store_true",
        default=None,
        help=(
            "Also publish locally in this run. Default behavior is CI-managed "
            "publish after commit/tag."
        ),
    )

    def release_handler(args: argparse.Namespace) -> None:
        """Run focused release flow centered on one-command UX."""

        def _truthy(val: object) -> bool:
            if isinstance(val, bool):
                return val
            if val is None:
                return False
            if isinstance(val, str):
                return val.lower() in ("1", "true", "yes", "y")
            return bool(val)

        dry_run = _truthy(
            resolve_setting(
                args.dry_run,
                "release_dry_run",
                env_keys=(
                    "PYFORGE_RELEASE_DRY_RUN",
                    "PYFORGE_RELEASE_INTEL_DRY_RUN",
                ),
                default=False,
            )
        )
        verbose_flag = _truthy(resolve_setting(args.verbose, "verbose", default=False))
        auto_confirm = _truthy(
            resolve_setting(
                args.yes,
                "auto_confirm",
                env_keys=("AUTO_CONFIRM", "PYFORGE_AUTO_CONFIRM"),
                default=False,
            )
        )
        local_publish = _truthy(
            resolve_setting(
                args.local_publish,
                "release_local_publish",
                env_keys=("PYFORGE_RELEASE_LOCAL_PUBLISH",),
                default=False,
            )
        )

        try:
            service = ReleaseService(project_root=os.getcwd())
            plan = service.plan(target_version=args.version)

            base_ref = plan.latest_tag or "initial commit"
            print(color_text(f"Analyzing commits since {base_ref}...", "blue"))
            print()
            print(color_text("Detected changes:", "cyan", bold=True))
            for item in plan.commits:
                summary = item.original_subject.strip() or item.summary
                print(f"- {summary} → {item.bump.upper()}")

            print()
            print(color_text(f"Suggested version: {plan.suggested_version}", "green"))
            print()
            print(color_text("Generated changelog:", "cyan", bold=True))
            print("---")
            print(plan.changelog_markdown)
            print("---")

            if dry_run:
                print(
                    color_text("[DRY RUN] No files or git refs were changed.", "yellow")
                )
                return

            should_continue = auto_confirm
            if not should_continue:
                response = input("Continue? (y/n) ").strip().lower()
                should_continue = response in {"y", "yes"}

            if not should_continue:
                print(color_text("Release cancelled.", "yellow"))
                return

            run_hooks("before_release", verbose=verbose_flag)
            service.apply(
                plan,
                local_publish=local_publish,
                dry_run=False,
            )
            run_hooks("after_release", verbose=verbose_flag)
            if local_publish:
                print(color_text("Release completed with local publish.", "green"))
            else:
                print(
                    color_text(
                        "Release committed and tagged. CI will handle publishing.",
                        "green",
                    )
                )
        except Exception as e:
            if os.environ.get("PYFORGE_DEBUG"):
                raise
            print(color_text(f"Release failed: {e}", "red"))
            sys.exit(1)

    release_parser.set_defaults(func=release_handler)

    # Show dependencies command
    deps_parser = subparsers.add_parser(
        "show-deps",
        help="Inspect detected project dependencies.",
        description="Display detected dependency files and pyproject.toml status.",
        formatter_class=HelpFormatter,
    )

    def show_deps_handler(args: argparse.Namespace) -> None:
        _warn_deprecated_command()
        report = detect_dependencies(os.getcwd())
        print(color_text("\nDependency Report:", "blue"))
        print(
            f"  {color_text('Has pyproject.toml:', 'yellow')} {report['has_pyproject']}"
        )
        req_files = (
            ", ".join(report["requirement_files"])
            if report["requirement_files"]
            else "None"
        )
        print(f"  {color_text('Requirement files:', 'yellow')} {req_files}")

    # Show entry point command
    entry_parser = subparsers.add_parser(
        "show-entry-point",
        help="Detect and show project entry point.",
        description="Auto-detect and display the main entry point for Docker/CLI builds.",  # noqa: E501
        formatter_class=HelpFormatter,
    )

    def show_entry_point_handler(args: argparse.Namespace) -> None:
        """Display detected entry point and alternatives."""
        _warn_deprecated_command()
        detected = detect_entry_point(os.getcwd())
        print(color_text("\nEntry Point Detection:", "blue"))

        if detected:
            print(f"  {color_text('Auto-detected entry point:', 'green')} {detected}")
        else:
            print(
                color_text(
                    "  No entry point detected (may not be a CLI project)",
                    "yellow",
                )
            )

        # Show alternatives
        alternatives = list_potential_entry_points(os.getcwd())
        if alternatives:
            print(f"\n  {color_text('Potential entry points:', 'cyan')}")
            for alt in alternatives:
                marker = "→" if alt == detected else " "
                print(f"    {marker} {alt}")
        else:
            print(f"  {color_text('No entry points found', 'yellow')}")

    def status_handler(args: argparse.Namespace) -> None:
        """Show project status including version and secrets."""
        _warn_deprecated_command()
        try:
            p_name, _ = get_project_details()
            local_ver = get_dynamic_version()
            pypi_ver = fetch_latest_version(p_name) or "Not Found"
            last_release = _get_last_release_tag()
            release_published_at = _get_last_release_published_at(last_release)

            pypi_token = resolve_setting(
                None, "pypi_token", env_keys=("PYPI_TOKEN",), default=None
            )
            docker_user = resolve_setting(
                None, "docker_user", env_keys=("DOCKERHUB_USERNAME",), default=None
            )
            docker_image = resolve_setting(
                None,
                "docker_image",
                env_keys=("DOCKER_IMAGE",),
                default=f"{docker_user}/{p_name}:{local_ver}" if docker_user else None,
            )
            docker_image_status = _check_docker_image_status(
                docker_image if isinstance(docker_image, str) else None
            )

            print(get_banner())
            print(color_text(f" Project: {p_name}".center(60), "blue", bold=True))
            print(color_text("─" * 60, "gray"))

            def print_row(label: str, value: str) -> None:
                print(f"  {label:<20} : {value}")

            v_color = "green" if local_ver != pypi_ver else "yellow"
            print_row("Local Version", color_text(local_ver, v_color))
            print_row("PyPI Version", pypi_ver)
            print_row("Last Release", last_release)
            print_row("Release Published", release_published_at)

            print(color_text("\n[ Authentication ]", "blue"))
            print_row(
                "PYPI_TOKEN",
                color_text("Set", "green")
                if pypi_token
                else color_text("Missing (OIDC available)", "yellow"),
            )
            print_row(
                "DOCKERHUB",
                color_text("Set", "green")
                if docker_user
                else color_text("Missing", "red"),
            )

            print(color_text("\n[ Docker ]", "blue"))
            print_row("Image", docker_image if isinstance(docker_image, str) else "N/A")
            docker_color = (
                "green"
                if docker_image_status == "Exists"
                else "yellow"
                if docker_image_status.startswith("Skipped")
                else "red"
            )
            print_row("Image Check", color_text(docker_image_status, docker_color))

            if local_ver == pypi_ver:
                print(
                    color_text(
                        (
                            "\nTip: Your local version matches PyPI. "
                            "Use --bump shame to release a new version."
                        ),
                        "yellow",
                    )
                )

            if not pypi_token:
                print(
                    color_text(
                        (
                            "\nWarning: PYPI_TOKEN is not set. "
                            "PyPI deployment will fail without it."
                        ),
                        "red",
                    )
                )

        except Exception as e:
            print(color_text(f"Error fetching status: {e}", "red"))

    status_parser = subparsers.add_parser(
        "status",
        help="Show project health and release readiness.",
        description=(
            "Reviews local vs PyPI versions, git repository cleanliness, "
            "and required environment tokens."
        ),
        formatter_class=HelpFormatter,
    )
    status_parser.set_defaults(func=status_handler)

    deps_parser.set_defaults(func=show_deps_handler)
    entry_parser.set_defaults(func=show_entry_point_handler)

    # Show version command
    version_parser = subparsers.add_parser(
        "show-version",
        help="Show resolved current project version.",
        description=(
            "Display the current project version as determined by\n"
            "pyproject.toml and version engine."
        ),
        formatter_class=HelpFormatter,
    )

    def show_version_handler(args: argparse.Namespace) -> None:
        _warn_deprecated_command()
        version = get_dynamic_version()
        print(color_text(f"\nCurrent project version: {version}", "green"))

    version_parser.set_defaults(func=show_version_handler)

    args = parser.parse_args()
    try:
        args.func(args)
    except PyForgeError as e:  # Domain-specific, user-friendly errors
        print(get_banner())
        print(color_text(f"Error: {e}", "red", bold=True))
        sys.exit(2)
    except Exception as e:  # Unexpected errors
        if os.environ.get("PYFORGE_DEBUG"):
            raise
        print(get_banner())
        print(color_text("Unexpected error occurred.", "red", bold=True))
        print(color_text(str(e), "red"))
        sys.exit(1)


if __name__ == "__main__":
    main()
