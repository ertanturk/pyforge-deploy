"""CLI module for pyforge_deploy."""

import argparse
import os
import sys

from pyforge_deploy.builders.docker import DockerBuilder
from pyforge_deploy.builders.docker_engine import detect_dependencies
from pyforge_deploy.builders.pypi import PyPIDistributor
from pyforge_deploy.builders.version_engine import get_dynamic_version
from pyforge_deploy.colors import color_text

EXAMPLES = """
Examples:
  pyforge-deploy docker-build --entry-point src/main.py --image-tag myapp:latest
  pyforge-deploy deploy-pypi --bump patch
  pyforge-deploy deploy-pypi --test --version 1.2.3
  pyforge-deploy show-deps
  pyforge-deploy show-version
"""


def main() -> None:
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

    docker_parser = subparsers.add_parser("docker-build", help="Build a Docker image.")
    docker_parser.add_argument("--entry-point", type=str, default=None)
    docker_parser.add_argument("--image-tag", type=str, default=None)
    docker_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging."
    )

    def docker_build_handler(args: argparse.Namespace) -> None:
        builder = DockerBuilder(
            entry_point=args.entry_point, image_tag=args.image_tag, verbose=args.verbose
        )
        try:
            builder.deploy()
        except Exception as e:
            if os.environ.get("PYFORGE_DEBUG"):
                raise
            print(color_text(f"Error: Docker build failed: {e}", "red"))
            sys.exit(1)

    docker_parser.set_defaults(func=docker_build_handler)

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
