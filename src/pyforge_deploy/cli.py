"""CLI module for pyforge_deploy."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from pyforge_deploy.builders.docker import DockerBuilder
from pyforge_deploy.builders.docker_engine import detect_dependencies
from pyforge_deploy.builders.pypi import PyPIDistributor
from pyforge_deploy.builders.version_engine import (
    fetch_latest_version,
    get_dynamic_version,
    get_project_details,
    get_tool_config,
)
from pyforge_deploy.colors import color_text
from pyforge_deploy.templates.workflows import GITHUB_RELEASE_YAML

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


def get_banner() -> str:
    line = color_text("━" * 60, "magenta")
    title = color_text("PYFORGE DEPLOY", "magenta", bold=True).center(70)
    return f"\n{line}\n{title}\n{line}"


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=get_banner(),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {color_text(get_dynamic_version(), 'green')}",
    )

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

    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Available commands"
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize pyforge-deploy GitHub Action workflow in the current project.",
        description="Creates a professional .github/workflows/pyforge-deploy.yml file.",
    )

    docker_parser = subparsers.add_parser(
        "docker-build",
        help="Build/Push Docker images",
        aliases=["docker", "build-docker"],
        description=(
            "Automatically scans project for dependencies, renders a Dockerfile, "
            "and builds an image."
        ),
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
        config = get_tool_config()

        do_push = args.push or config.get("docker_push", False)
        do_confirm = args.yes or config.get("auto_confirm", False)
        platforms = args.platforms or config.get("docker_platforms", None)
        if platforms is not None and not isinstance(platforms, str):
            platforms = str(platforms)

        builder = DockerBuilder(
            entry_point=args.entry_point,
            image_tag=args.image_tag,
            verbose=args.verbose,
            auto_confirm=bool(do_confirm),
            dry_run=args.dry_run,
            platforms=platforms,
        )
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
        help="Publish package to PyPI",
        aliases=["deploy", "pypi", "publish"],
        description=(
            "Calculates next version (PEP 440), builds wheel/sdist, "
            "and uploads using uv/twine."
        ),
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
        config = get_tool_config()
        bump_type = (
            args.bump if args.bump is not None else config.get("default_bump", "patch")
        )
        if not isinstance(bump_type, str) and bump_type is not None:
            bump_type = str(bump_type)
        do_confirm = args.yes or config.get("auto_confirm", False)
        distributor = PyPIDistributor(
            target_version=args.version,
            use_test_pypi=args.test,
            bump_type=bump_type,
            verbose=args.verbose,
            auto_confirm=bool(do_confirm),
            dry_run=args.dry_run,
        )
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
        help="Show detected project dependencies.",
        description="Display detected dependency files and pyproject.toml status.",
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

    def status_handler(args: argparse.Namespace) -> None:
        """Show project status including version and secrets."""
        try:
            p_name, _ = get_project_details()
            local_ver = get_dynamic_version()
            pypi_ver = fetch_latest_version(p_name) or "Not Found"

            pypi_token = os.environ.get("PYPI_TOKEN")
            docker_user = os.environ.get("DOCKERHUB_USERNAME")

            print(get_banner())
            print(color_text(f" Project: {p_name}".center(60), "blue", bold=True))
            print(color_text("─" * 60, "gray"))

            def print_row(label: str, value: str) -> None:
                print(f"  {label:<20} : {value}")

            v_color = "green" if local_ver != pypi_ver else "yellow"
            print_row("Local Version", color_text(local_ver, v_color))
            print_row("PyPI Version", pypi_ver)

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
        help="Check project health",
        description=(
            "Reviews local vs PyPI versions, git repository cleanliness, "
            "and required environment tokens."
        ),
    )
    status_parser.set_defaults(func=status_handler)

    deps_parser.set_defaults(func=show_deps_handler)

    # Show version command
    version_parser = subparsers.add_parser(
        "show-version",
        help="Show the current project version.",
        description=(
            "Display the current project version as determined by\n"
            "pyproject.toml and version engine."
        ),
    )

    def show_version_handler(args: argparse.Namespace) -> None:
        version = get_dynamic_version()
        print(color_text(f"\nCurrent project version: {version}", "green"))

    version_parser.set_defaults(func=show_version_handler)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
