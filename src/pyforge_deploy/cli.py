"""CLI module for pyforge_deploy."""

import argparse
import json
import os
import shutil
import subprocess  # nosec B404: subprocess usage is controlled, no shell=True
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from dotenv import load_dotenv

from pyforge_deploy.builders.docker import DockerBuilder
from pyforge_deploy.builders.docker_engine import detect_dependencies
from pyforge_deploy.builders.entry_point_detector import (
    detect_entry_point,
    list_potential_entry_points,
)
from pyforge_deploy.builders.pypi import PyPIDistributor
from pyforge_deploy.builders.version_engine import (
    fetch_latest_version,
    get_dynamic_version,
    get_project_details,
)
from pyforge_deploy.colors import color_text
from pyforge_deploy.config import resolve_setting
from pyforge_deploy.errors import PyForgeError
from pyforge_deploy.templates.workflows import GITHUB_RELEASE_YAML


class HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter
):
    """CLI help formatter with preserved layout and default values."""


def _log(message: str, color: str = "blue", verbose: bool = False) -> None:
    from pyforge_deploy.colors import color_text, is_ci_environment

    if verbose or is_ci_environment():
        print(color_text(f"[CLI] {message}", color))


EXAMPLES = f"""
{color_text("Quick Start Examples:", "magenta")}
  {color_text("Setup:", "blue")}
    pyforge-deploy init                             {color_text("# Initialize GitHub Actions & versioning", "gray", bold=False)}
    
  {color_text("Releasing:", "blue")}
    pyforge-deploy deploy-pypi                      {color_text("# Standard patch release (1.0.0 -> 1.0.1)", "gray", bold=False)}
    pyforge-deploy deploy-pypi --bump minor         {color_text("# Feature release (1.0.0 -> 1.1.0)", "gray", bold=False)}
    
  {color_text("Docker:", "blue")}
    pyforge-deploy docker-build --push              {color_text("# Auto-detect deps, build & push image", "gray", bold=False)}

  {color_text("Monitoring:", "blue")}
    pyforge-deploy status                           {color_text("# Check versions, Git & Secrets health", "gray", bold=False)}
"""  # noqa: E501

OVERVIEW = f"""
{color_text("Automate Python releases, packaging, and Docker image builds.", "cyan")}

{color_text("Typical workflow:", "blue")}
    1) pyforge-deploy init
    2) pyforge-deploy status
    3) pyforge-deploy deploy-pypi --bump patch
    4) pyforge-deploy docker-build --push

{color_text("Tip:", "yellow")} Use a subcommand with -h for focused help.
    pyforge-deploy docker-build -h
"""

DOCKER_EXAMPLES = f"""
{color_text("Examples:", "magenta")}
    pyforge-deploy docker-build
    pyforge-deploy docker-build --image-tag user/app:1.2.3
    pyforge-deploy docker-build --platforms linux/amd64,linux/arm64 --push
"""

PYPI_EXAMPLES = f"""
{color_text("Examples:", "magenta")}
    pyforge-deploy deploy-pypi
    pyforge-deploy deploy-pypi --bump minor
    pyforge-deploy deploy-pypi --version 1.2.0 --test
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


def main() -> None:
    load_dotenv()
    verbose = "--verbose" in sys.argv
    _log("Starting CLI main()", "magenta", verbose)
    parser = argparse.ArgumentParser(
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
        "--verbose", action="store_true", help="Detailed debug logging."
    )
    global_group.add_argument(
        "-y", "--yes", action="store_true", help="Non-interactive mode (Auto-confirm)."
    )
    _log("Added global arguments", "cyan", verbose)

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{init,docker-build,deploy-pypi,show-deps,show-entry-point,status,show-version}",
        help="Run 'pyforge-deploy <command> -h' for command-specific options.",
    )
    _log("Subparsers for commands added", "cyan", verbose)

    init_parser = subparsers.add_parser(
        "init",
        help="Bootstrap workflow and versioning files.",
        description=(
            "Initialize project automation by creating:\n"
            "- .github/workflows/pyforge-deploy.yml\n"
            "- .dockerignore (if missing)\n"
            "- version files when absent"
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
    docker_parser.add_argument("--entry-point", type=str, default=None)
    docker_parser.add_argument("--image-tag", type=str, default=None)
    docker_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging."
    )
    docker_parser.add_argument(
        "--push",
        action="store_true",
        help="Push the generated image to Docker Hub/Registry.",
    )
    docker_parser.add_argument(
        "-y", "--yes", action="store_true", help="Automatically say yes to prompts."
    )
    docker_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the process without making changes.",
    )
    docker_parser.add_argument(
        "--platforms",
        type=str,
        default=None,
        help=(
            "Comma-separated platforms (e.g., linux/amd64,linux/arm64) "
            "for multi-arch builds."
        ),
    )

    def init_handler(args: argparse.Namespace) -> None:
        workflow_dir = Path(".github/workflows")
        workflow_dir.mkdir(parents=True, exist_ok=True)
        target_path = workflow_dir / "pyforge-deploy.yml"

        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(GITHUB_RELEASE_YAML.strip())
            print(color_text(f"Successfully created: {target_path}", "green"))

            dockerignore_path = Path(".dockerignore")
            if not dockerignore_path.exists():
                ignore_content = (
                    ".git\n.venv\nvenv\nenv\n__pycache__/\n*.pyc\n*.pyo\n*.pyd\n"
                    ".pytest_cache/\n.tox/\nbuild/\ndist/\n*.egg-info/\n.env\ntests/\n"
                )
                with open(dockerignore_path, "w", encoding="utf-8") as f:
                    f.write(ignore_content)
                print(color_text(f"Successfully created: {dockerignore_path}", "green"))
            else:
                print(
                    color_text(f"{dockerignore_path} already exists, skipping.", "blue")
                )

            print(color_text("\nChecking project structure for versioning...", "blue"))
            try:
                p_name, p_version = get_project_details()
                pkg_name = p_name.replace("-", "_")
                initial_version = p_version if p_version != "dynamic" else "0.0.0"

                base_dir = Path.cwd()
                src_path = base_dir / "src" / pkg_name
                flat_path = base_dir / pkg_name

                target_pkg_dir = src_path if (base_dir / "src").exists() else flat_path
                target_pkg_dir.mkdir(parents=True, exist_ok=True)

                about_file = target_pkg_dir / "__about__.py"
                if not about_file.exists():
                    about_file.write_text(
                        f'__version__ = "{initial_version}"\n', encoding="utf-8"
                    )
                    print(
                        color_text(
                            f"Created missing version file: {about_file}", "green"
                        )
                    )
                else:
                    print(color_text(f"{about_file} already exists.", "blue"))

                cache_file = base_dir / ".version_cache"
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
                args.yes, "auto_confirm", env_keys=("AUTO_CONFIRM",), default=False
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
    pypi_parser.add_argument("--test", action="store_true")
    pypi_parser.add_argument(
        "--bump",
        choices=["major", "minor", "patch", "alpha", "beta", "rc"],
        default=None,
        help=(
            "Version bump type. Supports stable (major, minor, patch) "
            "and pre-releases (alpha, beta, rc)."
        ),
    )
    pypi_parser.add_argument("--version", type=str, default=None)
    pypi_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging."
    )
    pypi_parser.add_argument(
        "-y", "--yes", action="store_true", help="Non-interactive mode."
    )
    pypi_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate deployment without uploading or changing files.",
    )

    def deploy_pypi_handler(args: argparse.Namespace) -> None:
        bump_arg = args.bump
        if not bump_arg:
            try:
                from pyforge_deploy.builders.version_engine import suggest_bump_from_git

                bump_type = suggest_bump_from_git()
                _log(
                    f"Auto-detected bump type from Git history: {bump_type}", "magenta"
                )
            except Exception:
                bump_type = resolve_setting(None, "default_bump", default="patch")
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
                args.yes, "auto_confirm", env_keys=("AUTO_CONFIRM",), default=False
            )
        )
        dry_run = _truthy(
            resolve_setting(
                args.dry_run, "pypi_dry_run", env_keys=("PYPI_DRY_RUN",), default=False
            )
        )
        verbose_flag = _truthy(resolve_setting(args.verbose, "verbose", default=False))

        # Keep constructor call minimal for test compatibility
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
        except Exception as e:
            if os.environ.get("PYFORGE_DEBUG"):
                raise
            print(color_text(f"PyPI deployment failed: {e}", "red"))
            sys.exit(1)

    pypi_parser.set_defaults(func=deploy_pypi_handler)

    # Show dependencies command
    deps_parser = subparsers.add_parser(
        "show-deps",
        help="Inspect detected project dependencies.",
        description="Display detected dependency files and pyproject.toml status.",
        formatter_class=HelpFormatter,
    )

    def show_deps_handler(args: argparse.Namespace) -> None:
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
                            "Use --bump to release a new version."
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
