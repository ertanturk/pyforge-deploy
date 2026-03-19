# nosec B404: subprocess usage is safe, no shell=True, command is a list
import fnmatch
import os
import subprocess  # nosec
import sys as _sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pyforge_deploy.colors import color_text, is_ci_environment
from pyforge_deploy.config import resolve_setting
from pyforge_deploy.errors import ConfigError, DockerBuildError
from pyforge_deploy.logutil import log as logutil
from pyforge_deploy.logutil import status_bar

from .docker_engine import detect_dependencies, get_python_version
from .entry_point_detector import detect_entry_point
from .version_engine import get_project_details

_sys.modules.setdefault("src.pyforge_deploy.builders.docker", _sys.modules[__name__])


class DockerBuilder:
    """
    Main class responsible for rendering the Dockerfile template
    and building the Docker image.

    Implements modern Docker best practices:
    - Multi-stage builds for smaller images
    - Layer caching and .dockerignore for efficient builds
    - pip cache and chain commands for dependency install
    - BuildKit support for advanced features (mount, cache, etc.)
    - Dependency prioritization for optimal cache usage
    """

    def __init__(
        self,
        entry_point: str | None = None,
        image_tag: str | None = None,
        verbose: bool = False,
        auto_confirm: bool = False,
        dry_run: bool = False,
        platforms: str | None = None,
    ) -> None:
        self.base_dir: Path = Path.cwd()
        self.verbose: bool = verbose
        self.auto_confirm: bool = auto_confirm
        self.dry_run: bool = dry_run
        self.platforms: str | None = platforms

        # Resolve image_tag: CLI -> pyproject.toml -> env -> derived default
        tool_image = resolve_setting(
            image_tag, "docker_image", env_keys=("DOCKER_IMAGE",)
        )
        if tool_image:
            self.image_tag = tool_image
        else:
            try:
                from .version_engine import get_dynamic_version

                p_name, _ = get_project_details()
                p_ver = get_dynamic_version()

                user = resolve_setting(
                    None, "docker_user", env_keys=("DOCKERHUB_USERNAME",)
                )
                if user:
                    self.image_tag = f"{user}/{p_name}:{p_ver}"
                else:
                    self.image_tag = f"{p_name}:{p_ver}"
            except Exception:
                self.image_tag = self.base_dir.name.lower().replace(" ", "-")
        self._log(
            (
                f"DockerBuilder initialized with entry_point={entry_point}, "
                f"image_tag={self.image_tag}, verbose={verbose}"
            ),
            "magenta",
        )
        self._log(f"Current working directory: {self.base_dir}", "magenta")

        self._validate_image_tag(self.image_tag)
        if entry_point:
            # Validate entry point to ensure it's safe for Docker CMD usage
            self._validate_entry_point(entry_point)
        else:
            # Auto-detect entry point if not provided (zero-config usability)
            detected = detect_entry_point(str(self.base_dir))
            if detected:
                self._log(f"Auto-detected entry point: {detected}", "green")
                self._validate_entry_point(detected)
                entry_point = detected

        # platforms: CLI -> pyproject -> env -> None
        self.entry_point: str | None = entry_point
        self.platforms = resolve_setting(
            platforms, "docker_platforms", env_keys=("DOCKER_PLATFORMS",)
        )
        self.dockerfile_path: Path = self.base_dir / "Dockerfile"
        self.req_docker_path: Path = self.base_dir / "requirements-docker.txt"
        self.heavy_req_path: Path = self.base_dir / "heavy-requirements.txt"

    def _log(self, message: str, color: str = "blue") -> None:
        """Helper to log messages only if verbose mode or CI is enabled."""
        # Always emit logs to aid users and tests; verbosity flags control
        # lower-level behavior elsewhere.
        logutil(f"[DockerBuilder] {message}", level="info", color=color)

    @staticmethod
    def _to_bool(value: object) -> bool:
        """Convert common config/env representations to bool.

        Handles booleans, numeric strings, and on/off style values so
        environment flags like ``PYFORGE_DOCKER_WHEELHOUSE=0`` behave as expected.
        """
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def _should_disable_wheelhouse_for_platforms(self) -> bool:
        """Return True when wheelhouse should be disabled for platform safety.

        Local wheelhouse artifacts are built on the host architecture. For
        multi-platform and ARM-targeted builds (commonly executed on amd64 CI
        runners via emulation), those wheels can be incompatible and break
        offline installs in Docker build stages.
        """
        if not self.platforms:
            return False

        platforms = [p.strip().lower() for p in self.platforms.split(",") if p.strip()]
        if not platforms:
            return False

        if len(platforms) > 1:
            return True

        single = platforms[0]
        return "arm64" in single or "arm/v" in single

    def _confirm(self, message: str) -> None:
        # Avoid blocking during CI and test runs or when explicitly auto_confirmed
        import sys as _sys

        if self.auto_confirm or is_ci_environment() or not _sys.stdin.isatty():
            return

        response = input(color_text(f"{message} [y/N]: ", "yellow")).strip().lower()
        if response not in ["y", "yes"]:
            print(color_text("Docker build cancelled by user.", "red"))
            _sys.exit(0)

    def _validate_image_tag(self, tag: str) -> None:
        """Validates image_tag for Docker safety."""
        valid_chars = "-./_:"
        sanitized = tag
        for char in valid_chars:
            sanitized = sanitized.replace(char, "")
        if not sanitized.isalnum():
            raise ConfigError(
                color_text(
                    f"Error: image_tag '{tag}' must be alphanumeric. "
                    "Only alphanumeric and (- . / _ :) are allowed.",
                    "red",
                )
            )

    def _validate_entry_point(self, entry: str) -> None:
        """Validate entry point string for simple safety."""
        valid_chars = "-./_:"
        sanitized = entry
        for char in valid_chars:
            sanitized = sanitized.replace(char, "")

        if not sanitized.isalnum():
            raise ConfigError(
                color_text(
                    f"Error: entry_point '{entry}' must be alphanumeric. "
                    "Only alphanumeric and (- . / _ :) are allowed.",
                    "red",
                )
            )

    def _generate_docker_requirements(
        self, remaining: list[str], heavy: list[str] | None = None
    ) -> None:
        """Writes separate requirement files:

        - `heavy-requirements.txt` for large packages (improves layer caching)
        - `requirements-docker.txt` for remaining runtime dependencies
        """
        heavy = heavy or []
        self._log("--- Detected Docker Requirements ---", "blue")
        if heavy:
            self._log(" Heavy hitters:", "blue")
            for pkg in heavy:
                self._log(f"  -> {pkg}", "blue")
        if remaining:
            self._log(" Remaining packages:", "blue")
            for pkg in remaining:
                self._log(f"  -> {pkg}", "blue")
        if not heavy and not remaining:
            self._log(" (No external dependencies needed!)", "blue")
        self._log("------------------------------------", "blue")

        if self.dry_run:
            self._log(
                (
                    f"[DRY RUN] Would write requirements to "
                    f"{self.req_docker_path} and {self.heavy_req_path}"
                ),
                "yellow",
            )
            return

        try:
            # write heavy hitters first
            if heavy:
                with open(self.heavy_req_path, "w", encoding="utf-8") as hf:
                    hf.write("# Heavy hitters auto-generated by pyforge-deploy\n")
                    for pkg in heavy:
                        hf.write(f"{pkg}\n")

            if not self.req_docker_path.exists():
                with open(self.req_docker_path, "a", encoding="utf-8"):
                    pass

            # write remaining requirements
            with open(self.req_docker_path, "w", encoding="utf-8") as f:
                f.write("# Auto-generated by pyforge-deploy AST/Venv scan\n")
                if remaining:
                    for pkg in remaining:
                        f.write(f"{pkg}\n")
        except Exception as err:
            self._log(f"Error: Failed to write requirements files: {err}", "red")
            raise DockerBuildError(
                color_text(f"Error: Failed to write requirements files: {err}", "red")
            ) from err

    def _ensure_dockerignore_sanity(self) -> None:
        """Ensure a sane `.dockerignore` exists and contains critical entries.

        This minimizes Docker build context by adding missing critical ignores
        while avoiding duplicates. Respects `dry_run` by only logging what would
        change.
        """
        critical_ignores = {
            ".git",
            ".venv",
            "venv",
            "env",
            "__pycache__",
            "build",
            "dist",
            "*.egg-info",
            ".env",
            ".pytest_cache",
            ".tox",
            "tests",
            ".pyforge-deploy-cache",
        }

        path = self.base_dir / ".dockerignore"

        existing_lines: list[str] = []
        if path.exists():
            existing_lines = [
                line.split("#", 1)[0].strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        # Normalize existing entries for comparison (strip trailing slashes)
        normalized = {ln.rstrip("/") for ln in existing_lines if ln}

        to_add: list[str] = []
        for ci in sorted(critical_ignores):
            norm_ci = ci.rstrip("/")
            found = False
            # Exact/normalized match
            if norm_ci in normalized:
                found = True
            else:
                # Wildcard-aware match
                for ln in existing_lines:
                    if not ln:
                        continue
                    try:
                        if fnmatch.fnmatchcase(ln, ci) or fnmatch.fnmatchcase(
                            ln, norm_ci
                        ):
                            found = True
                            break
                    except (TypeError, ValueError) as e:
                        # If a pattern or line is malformed, skip and continue scanning.
                        self._log(
                            f"Invalid .dockerignore pattern/line: {ln}: {e}", "yellow"
                        )
                        continue
            if not found:
                to_add.append(ci)

        if not to_add:
            self._log(".dockerignore looks good — no changes needed.", "green")
            return

        if self.dry_run:
            self._log(
                f"[DRY RUN] Would add {len(to_add)} entries to .dockerignore: {to_add}",
                "yellow",
            )
            return

        # Append missing entries with a small header
        try:
            # Ensure file exists
            if not path.exists():
                path.write_text(
                    "# .dockerignore generated by pyforge-deploy\n", encoding="utf-8"
                )

            with path.open("a", encoding="utf-8") as f:
                f.write("\n# Added by pyforge-deploy\n")
                for item in to_add:
                    f.write(f"{item}\n")

            self._log(f"Added {len(to_add)} missing entries to .dockerignore.", "green")
        except Exception as err:
            self._log(f"Failed to update .dockerignore: {err}", "red")
            raise DockerBuildError(
                color_text(f"Failed to update .dockerignore: {err}", "red")
            ) from err

    def _build_wheelhouse(self, report: dict[str, Any]) -> None:
        """Build a local wheelhouse directory from requirements to speed Docker builds.

        This runs `pip wheel` to produce wheels under `./wheels`.
        """
        wheels_dir = self.base_dir / "wheels"
        try:
            wheels_dir.mkdir(exist_ok=True)
        except Exception as e:
            raise DockerBuildError(
                color_text(f"Failed to create wheels dir: {e}", "red")
            ) from e

        commands: list[list[str]] = []
        # Build wheels for remaining requirements
        if self.req_docker_path.exists():
            commands.append(
                [
                    _sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "-r",
                    str(self.req_docker_path),
                    "-w",
                    str(wheels_dir),
                ]
            )
        # Build wheels for heavy hitters separately
        if self.heavy_req_path.exists():
            commands.append(
                [
                    _sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "-r",
                    str(self.heavy_req_path),
                    "-w",
                    str(wheels_dir),
                ]
            )

        for cmd in commands:
            try:
                self._log(f"Running: {' '.join(cmd)}", "cyan")
                subprocess.run(cmd, check=True, cwd=str(self.base_dir))  # nosec B603
            except subprocess.CalledProcessError as err:
                raise DockerBuildError(
                    color_text(f"Wheelhouse build failed: {err}", "red")
                ) from err

    def render_template(self) -> None:
        """Renders the Dockerfile template based on detected dependencies."""
        try:
            self._ensure_dockerignore_sanity()
        except Exception as err:
            self._log(f"Warning: .dockerignore optimization failed: {err}", "yellow")
        configured_python = resolve_setting(
            None, "docker_python", env_keys=("PYFORGE_PYTHON_VERSION", "PYTHON_VERSION")
        )
        python_version: str = configured_python or get_python_version()
        if not python_version.endswith("slim"):
            python_image = f"{python_version}-slim"
        else:
            python_image = python_version

        if not self.entry_point:
            from .docker_engine import detect_entry_point

            detected_entry = detect_entry_point(str(self.base_dir))
            if detected_entry:
                self.entry_point = detected_entry
                self._log(
                    f"Auto-configured Docker CMD to run: {self.entry_point}", "magenta"
                )

        self._log(
            f"Rendering Dockerfile template with python_image={python_image}",
            "cyan",
        )
        try:
            report: dict[str, Any] = detect_dependencies(str(self.base_dir))
            try:
                self._generate_docker_requirements(
                    report.get("final_list", []), report.get("heavy_hitters", [])
                )
            except Exception as err:
                raise DockerBuildError(
                    color_text(
                        "Failed to render Dockerfile template: "
                        "requirements write failed",
                        "red",
                    )
                ) from err

            use_wheelhouse_flag = resolve_setting(
                None, "docker_wheelhouse", env_keys=("PYFORGE_DOCKER_WHEELHOUSE",)
            )
            use_wheelhouse = self._to_bool(use_wheelhouse_flag)
            if use_wheelhouse and self._should_disable_wheelhouse_for_platforms():
                self._log(
                    "Disabling local wheelhouse for multi-platform/ARM build safety.",
                    "yellow",
                )
                use_wheelhouse = False
            try:
                if use_wheelhouse and not self.dry_run:
                    self._log("Building local wheelhouse for Docker build", "cyan")
                    self._build_wheelhouse(report)
            except DockerBuildError:
                # Non-fatal: allow rendering to continue using online installs
                self._log(
                    "Wheelhouse build failed; continuing without wheelhouse", "yellow"
                )
        except Exception:
            report = {
                "has_pyproject": False,
                "requirement_files": [],
                "final_list": [],
                "heavy_hitters": [],
                "detected_imports": [],
                "dev_tools": [],
            }
            try:
                self._generate_docker_requirements([], [])
            except Exception as err:
                raise DockerBuildError(
                    color_text(
                        (
                            "Failed to render Dockerfile template: "
                            "requirements write failed"
                        ),
                        "red",
                    )
                ) from err
        current_module_dir: Path = Path(__file__).parent
        templates_dir: Path = current_module_dir.parent / "templates"
        self._log(f"Templates directory: {templates_dir}", "magenta")
        if not templates_dir.exists():
            self._log(f"Error: Templates directory not found at {templates_dir}", "red")
            raise FileNotFoundError(
                color_text(
                    f"Error: Templates directory not found at {templates_dir}", "red"
                )
            )
        env: Environment = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["j2", "html", "xml"]),
        )
        try:
            template = env.get_template("Dockerfile.j2")
            self._log("Loaded Dockerfile.j2 template.", "cyan")
            # Resolve optional features from config: wheelhouse and non-root final image
            use_wheelhouse_flag = resolve_setting(
                None, "docker_wheelhouse", env_keys=("PYFORGE_DOCKER_WHEELHOUSE",)
            )
            use_wheelhouse = self._to_bool(use_wheelhouse_flag)
            if use_wheelhouse and self._should_disable_wheelhouse_for_platforms():
                use_wheelhouse = False
            non_root_flag = resolve_setting(
                None, "docker_non_root", env_keys=("PYFORGE_DOCKER_NON_ROOT",)
            )

            rendered_content: str = template.render(
                python_image=python_image,
                python_version=python_version,
                report=report,
                entry_point=self.entry_point,
                use_wheelhouse=use_wheelhouse,
                non_root=self._to_bool(non_root_flag),
            )
            self._log("Dockerfile template rendered successfully.", "green")
        except Exception as err:
            self._log(f"Error: Failed to render Dockerfile template: {err}", "red")
            raise DockerBuildError(
                color_text(f"Error: Failed to render Dockerfile template: {err}", "red")
            ) from err

        if self.dry_run:
            self._log(
                f"[DRY RUN] Would write rendered Dockerfile to {self.dockerfile_path}",
                "yellow",
            )
            return

        try:
            with open(self.dockerfile_path, "w", encoding="utf-8") as f:
                f.write(rendered_content)
            self._log(f"Dockerfile written to {self.dockerfile_path}", "green")
        except Exception as err:
            self._log(f"Error: Failed to write Dockerfile: {err}", "red")
            raise DockerBuildError(
                color_text(f"Error: Failed to write Dockerfile: {err}", "red")
            ) from err

    def build_image(self, push: bool = False) -> None:
        self._log(
            f"Building Docker image with tag: '{self.image_tag}'...",
            "blue",
        )

        image_name = self.image_tag.split(":")[0]
        cache_ref = f"{image_name}:latest"

        if os.environ.get("GITHUB_ACTIONS") == "true":
            self._log("GitHub Actions environment detected. Using 'gha' cache.", "cyan")
            cache_from_arg = "type=gha"
            cache_to_arg = "type=gha,mode=max"
        else:
            self._log(
                "Local environment detected. Using 'registry/inline' cache.", "cyan"
            )
            cache_from_arg = f"type=registry,ref={cache_ref}"
            cache_to_arg = "type=inline"

        if self.platforms:
            self._log(f"Multi-platform build enabled for: {self.platforms}", "cyan")
            cmd: list[str] = [
                "docker",
                "buildx",
                "build",
                "--platform",
                self.platforms,
                "--pull",
                "--rm",
                "--cache-from",
                cache_from_arg,
                "--cache-to",
                cache_to_arg,
                "-t",
                self.image_tag,
                ".",
            ]
            if push or is_ci_environment():
                cmd.append("--push")
                self._log(
                    "Buildx will automatically push the image after building.", "cyan"
                )
        else:
            cmd = [
                "docker",
                "build",
                "--pull",
                "--rm",
                "--cache-from",
                cache_from_arg,
                "-t",
                self.image_tag,
                ".",
            ]

        if self.dry_run:
            self._log(f"[DRY RUN] Would execute: {' '.join(cmd)}", "yellow")
            return

        env = os.environ.copy()
        env["DOCKER_BUILDKIT"] = "1"
        env["BUILDKIT_PROGRESS"] = env.get("BUILDKIT_PROGRESS", "plain")

        try:
            self._log(f"Build command: {' '.join(cmd)}", "cyan")
            subprocess.run(cmd, check=True, cwd=str(self.base_dir), env=env)  # nosec B603
            self._log(
                f"Docker image '{self.image_tag}' built successfully!",
                "green",
            )
            if self.req_docker_path.exists():
                self._log("Cleaning up requirements-docker.txt", "gray")
                self.req_docker_path.unlink()
            if self.heavy_req_path.exists():
                self._log("Cleaning up heavy-requirements.txt", "gray")
                self.heavy_req_path.unlink()
        except subprocess.CalledProcessError as err:
            self._log(f"Error: Docker build failed: {err}", "red")
            raise DockerBuildError(
                color_text("Docker build process failed. Check the logs above.", "red")
            ) from err
        except FileNotFoundError as err:
            self._log(f"Error: Docker executable not found: {err}", "red")
            raise DockerBuildError(
                color_text(
                    "Docker executable not found. Ensure Docker is installed.", "red"
                )
            ) from err
        finally:
            if self.req_docker_path.exists():
                self._log(
                    f"Cleaning up temporary file: {self.req_docker_path.name}", "gray"
                )
                self.req_docker_path.unlink()
            if self.heavy_req_path.exists():
                self._log(
                    f"Cleaning up temporary file: {self.heavy_req_path.name}", "gray"
                )
                self.heavy_req_path.unlink()

    def push_image(self) -> None:
        """Pushes the built Docker image to the registry."""
        self._log(f"Pushing Docker image: '{self.image_tag}'...", "blue")

        cmd: list[str] = ["docker", "push", self.image_tag]

        if self.dry_run:
            self._log(f"[DRY RUN] Would execute: {' '.join(cmd)}", "yellow")
            return

        self._log(f"Push command: {' '.join(cmd)}", "cyan")

        try:
            subprocess.run(cmd, check=True, cwd=str(self.base_dir))  # nosec B603
            self._log(
                f"Docker image '{self.image_tag}' pushed successfully!",
                "green",
            )
            print(
                color_text(
                    f"Docker image '{self.image_tag}' pushed successfully!", "green"
                )
            )
        except subprocess.CalledProcessError as err:
            self._log(f"Error: Docker push failed: {err}", "red")
            raise DockerBuildError(
                color_text("Docker push process failed. Check the logs above.", "red")
            ) from err
        except FileNotFoundError as err:
            self._log(f"Error: Docker executable not found: {err}", "red")
            raise DockerBuildError(
                color_text(
                    "Docker executable not found. Ensure Docker is installed.", "red"
                )
            ) from err

    def deploy(self, push: bool = False) -> None:
        """Main method to render Dockerfile and build the image."""
        self._log("Starting Docker deployment process...", "magenta")

        should_push = (push or is_ci_environment()) and not self.platforms
        total_steps = 4 if should_push else 3

        status_bar(1, total_steps, "Preparing Docker deployment")
        action = "build and PUSH" if (push or is_ci_environment()) else "build"
        if not self.dry_run:
            self._confirm(
                f"Do you want to {action} the Docker image '{self.image_tag}'?"
            )

        status_bar(2, total_steps, "Rendering Dockerfile")
        self.render_template()

        status_bar(3, total_steps, "Building Docker image")
        self.build_image(push=push)

        if should_push:
            status_bar(4, total_steps, "Pushing Docker image")
            self._log("Standard push requested.", "magenta")
            self.push_image()

        self._log("Docker deployment flow completed.", "green")
