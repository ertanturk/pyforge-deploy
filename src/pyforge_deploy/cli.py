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
)
from pyforge_deploy.colors import color_text
from pyforge_deploy.templates.workflows import GITHUB_RELEASE_YAML

EXAMPLES = """
Examples:
  pyforge-deploy docker-build --entry-point src/main.py --image-tag myapp:latest
  pyforge-deploy deploy-pypi --bump patch
  pyforge-deploy deploy-pypi --test --version 1.2.3
  pyforge-deploy show-deps
  pyforge-deploy show-version
"""


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="PyForge Deploy CLI",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging for CI/CD debugging.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Automatically say yes to all prompts (Non-interactive mode).",
    )

    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Available commands"
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize pyforge-deploy GitHub Action workflow in the current project.",
        description="Creates a professional .github/workflows/pyforge-deploy.yml file.",
    )

    docker_parser = subparsers.add_parser("docker-build", help="Build a Docker image.")
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

    def init_handler(args: argparse.Namespace) -> None:
        workflow_dir = Path(".github/workflows")
        workflow_dir.mkdir(parents=True, exist_ok=True)

        target_path = workflow_dir / "pyforge-deploy.yml"

        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(GITHUB_RELEASE_YAML.strip())

            print(color_text(f"Successfully created: {target_path}", "green"))
            print(color_text("Next Steps:", "blue"))
            print(
                color_text(
                    "1. Go to your GitHub Repository Settings > Secrets.", "yellow"
                )
            )
            print(
                color_text(
                    "2. Add 'PYPI_TOKEN', 'DOCKERHUB_USERNAME', and 'DOCKERHUB_TOKEN'.",
                    "yellow",
                )
            )
            print(
                color_text("3. Push your changes and watch the magic happen!", "yellow")
            )

        except Exception as e:
            print(color_text(f"Error: Could not create workflow file: {e}", "red"))

    def docker_build_handler(args: argparse.Namespace) -> None:
        builder = DockerBuilder(
            entry_point=args.entry_point,
            image_tag=args.image_tag,
            verbose=args.verbose,
            auto_confirm=args.yes,
        )
        try:
            builder.deploy(push=args.push)
        except Exception as e:
            if os.environ.get("PYFORGE_DEBUG"):
                raise
            print(color_text(f"Error: Docker build failed: {e}", "red"))
            sys.exit(1)

    docker_parser.set_defaults(func=docker_build_handler)
    init_parser.set_defaults(func=init_handler)

    pypi_parser = subparsers.add_parser("deploy-pypi", help="Deploy to PyPI.")
    pypi_parser.add_argument("--test", action="store_true")
    pypi_parser.add_argument(
        "--bump", choices=["major", "minor", "patch"], default=None
    )
    pypi_parser.add_argument("--version", type=str, default=None)
    pypi_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging."
    )
    pypi_parser.add_argument(
        "-y", "--yes", action="store_true", help="Non-interactive mode."
    )

    def deploy_pypi_handler(args: argparse.Namespace) -> None:
        distributor = PyPIDistributor(
            target_version=args.version,
            use_test_pypi=args.test,
            bump_type=args.bump,
            verbose=args.verbose,
            auto_confirm=args.yes,
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

            print(color_text(f"\n---PyForge Project Status: '{p_name}' ---", "blue"))

            v_color = "green" if local_ver != pypi_ver else "yellow"
            print(f"  Local Version:  {color_text(local_ver, v_color)}")
            print(f"  PyPI Version:   {pypi_ver}")

            print("\n  Secrets Check:")
            t_status = (
                color_text("Set", "green")
                if pypi_token
                else color_text("Missing", "red")
            )
            d_status = (
                color_text("Set", "green")
                if docker_user
                else color_text("Missing", "yellow")
            )
            print(f"  - PYPI_TOKEN:           {t_status}")
            print(f"  - DOCKERHUB_USERNAME:   {d_status}")

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

            print(color_text("------------------------------------------\n", "blue"))
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
        "status", help="Show project and deployment status"
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
