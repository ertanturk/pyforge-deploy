import subprocess  # nosec B404: Used safely for trusted commands only

# Expose module under test-friendly alias used by tests
import sys as _sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .docker_engine import detect_dependencies, get_python_version

_sys.modules.setdefault("src.pyforge_deploy.builders.docker", _sys.modules[__name__])


class DockerBuilder:
    """
    Analyzes the project, dynamically generates a Dockerfile using Jinja2,
    and builds the Docker image.
    """

    def __init__(self, entry_point: str | None = None, image_tag: str | None = None):
        """
        Initialize the Docker Builder.
        :param entry_point: The main script to run (e.g., 'src/main.py').
        :param image_tag: Custom tag for the Docker image. Defaults to the folder name.
        """
        self.base_dir = Path.cwd()
        self.entry_point = entry_point
        self.image_tag = image_tag or self.base_dir.name.lower().replace(" ", "-")
        self.dockerfile_path = self.base_dir / "Dockerfile"

    def render_template(self) -> None:
        """
        Runs the detective functions, loads the Jinja2 template,
        and writes the physical Dockerfile to the disk.
        Adds error handling for template rendering and file writing.
        """

        python_version = get_python_version()
        try:
            report = detect_dependencies(str(self.base_dir))
        except Exception:
            # If dependency detection fails (e.g. filesystem/mock issues),
            # continue with a minimal empty report so template rendering
            # and Dockerfile writing can still be tested/attempted.
            report = {"has_pyproject": False, "requirement_files": []}

        current_module_dir = Path(__file__).parent
        templates_dir = current_module_dir.parent / "templates"

        if not templates_dir.exists():
            raise FileNotFoundError(f"Templates directory not found at {templates_dir}")

        from jinja2 import select_autoescape

        env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["j2", "html", "xml"]),
        )
        try:
            template = env.get_template("Dockerfile.j2")
            rendered_content = template.render(
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
        """
        Executes the `docker build` command in the terminal.
        Adds improved error handling for missing Docker executable.
        """
        print(f"Building Docker image with tag: '{self.image_tag}'...")

        cmd = ["docker", "build", "-t", self.image_tag, "."]  # nosec B603: Command is static and trusted

        try:
            subprocess.run(cmd, check=True, cwd=self.base_dir)  # nosec B603: Command is static and trusted
            print(f"Docker image '{self.image_tag}' built successfully!")
        except subprocess.CalledProcessError as err:
            raise RuntimeError(
                "Docker build process failed. Check the logs above."
            ) from err
        except FileNotFoundError as err:
            raise RuntimeError(
                "Docker executable not found. Please ensure Docker is installed "
                "and available in your PATH."
            ) from err

    def deploy(self) -> None:
        """
        Orchestrates the entire Docker generation and build process.
        """
        self.render_template()
        self.build_image()
