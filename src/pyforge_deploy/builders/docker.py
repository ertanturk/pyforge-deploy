# nosec B404: subprocess usage is safe, no shell=True, command is a list
import subprocess  # nosec
import sys as _sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pyforge_deploy.colors import color_text

from .docker_engine import detect_dependencies, get_python_version

_sys.modules.setdefault("src.pyforge_deploy.builders.docker", _sys.modules[__name__])


class DockerBuilder:
    """Main class responsible for rendering the Dockerfile template
    and building the Docker image.
    """

    def __init__(
        self, entry_point: str | None = None, image_tag: str | None = None
    ) -> None:
        self.base_dir: Path = Path.cwd()
        # Validate entry_point for Docker safety
        if (
            entry_point is not None
            and not entry_point.replace("_", "").replace("-", "").isalnum()
        ):
            raise ValueError("entry_point must be alphanumeric, underscore, or hyphen")
        self.entry_point: str | None = entry_point
        # Validate image_tag for Docker safety
        if image_tag is not None and not image_tag.replace("-", "").isalnum():
            raise ValueError("image_tag must be alphanumeric or hyphen")
        self.image_tag: str = image_tag or self.base_dir.name.lower().replace(" ", "-")
        self.dockerfile_path: Path = self.base_dir / "Dockerfile"

    def render_template(self) -> None:
        """Renders the Dockerfile template based on detected dependencies."""
        python_version: str = get_python_version()

        try:
            report: dict[str, Any] = detect_dependencies(str(self.base_dir))
        except Exception:
            report = {
                "has_pyproject": False,
                "requirement_files": [],
                "final_list": [],
                "detected_imports": [],
                "dev_tools": [],
            }

        current_module_dir: Path = Path(__file__).parent
        templates_dir: Path = current_module_dir.parent / "templates"

        if not templates_dir.exists():
            raise FileNotFoundError(f"Templates directory not found at {templates_dir}")

        env: Environment = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["j2", "html", "xml"]),
        )

        try:
            template = env.get_template("Dockerfile.j2")
            rendered_content: str = template.render(
                python_version=python_version,
                report=report,
                entry_point=self.entry_point,
            )
        except Exception as err:
            raise RuntimeError(f"Failed to render Dockerfile template: {err}") from err

        try:
            with open(self.dockerfile_path, "w", encoding="utf-8") as f:
                f.write(rendered_content)
        except Exception as err:
            raise RuntimeError(f"Failed to write Dockerfile: {err}") from err

    def build_image(self) -> None:
        """Builds the Docker image using the rendered Dockerfile."""
        print(
            color_text(f"Building Docker image with tag: '{self.image_tag}'...", "blue")
        )

        cmd: list[str] = ["docker", "build", "-t", self.image_tag, "."]  # nosec B603: no user input, safe

        try:
            subprocess.run(cmd, check=True, cwd=str(self.base_dir))  # nosec B603
            print(
                color_text(
                    f"Docker image '{self.image_tag}' built successfully!", "green"
                )
            )
        except subprocess.CalledProcessError as err:
            print(f"[ERROR] Docker build failed: {err}")
            raise RuntimeError(
                "Docker build process failed. Check the logs above."
            ) from err
        except FileNotFoundError as err:
            print(f"[ERROR] Docker executable not found: {err}")
            raise RuntimeError(
                "Docker executable not found. Please ensure Docker is installed "
                "and available in your PATH."
            ) from None

    def deploy(self) -> None:
        """Main method to render Dockerfile and build the image."""
        self.render_template()
        self.build_image()
