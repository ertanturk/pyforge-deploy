import os
import shutil
import subprocess  # nosec B404: subprocess usage is safe, no shell=True, trusted args
import sys
from pathlib import Path

from dotenv import load_dotenv

from pyforge_deploy.colors import color_text

from .version_engine import get_dynamic_version


class PyPIDistributor:
    """
    Handles building and distributing Python packages to PyPI or TestPyPI.
    Ensures environment, version, and token are valid.
    Logs errors for troubleshooting.
    """

    def __init__(
        self,
        target_version: str | None = None,
        use_test_pypi: bool = False,
        bump_type: str | None = None,
    ):
        """
        Initialize distributor.
        :param target_version: Manually set version to deploy.
        :param use_test_pypi: Deploy to TestPyPI if True, else PyPI.
        :param bump_type: Version bump type ('major', 'minor', 'patch').
        """
        self.target_version = target_version
        self.bump_type = bump_type
        self.repository = "testpypi" if use_test_pypi else "pypi"
        self.base_dir = Path.cwd()

        env_path = self.base_dir / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
        else:
            print(
                color_text(
                    f"Warning: .env file not found at {env_path}. PYPI_TOKEN may be missing.",  # noqa: E501
                    "yellow",
                )
            )
        self.token = os.environ.get("PYPI_TOKEN")

    def _clean_dist(self) -> None:
        """
        Remove build artifacts, dist directory, and egg-info to prevent version caching.
        """
        # Define paths to clean based on project structure
        paths_to_clean = [
            self.base_dir / "dist",
            self.base_dir / "build",
        ]

        # Dynamically find and add any .egg-info directories
        paths_to_clean.extend(self.base_dir.glob("*.egg-info"))
        paths_to_clean.extend((self.base_dir / "src").glob("*.egg-info"))

        for path in paths_to_clean:
            if path.exists():
                shutil.rmtree(path) if path.is_dir() else path.unlink()

    def deploy(self) -> None:
        """
        Build and upload package to PyPI/TestPyPI.
        Handles token, version, build, upload, and logs errors.
        """
        if not self.token:
            print(color_text("Error: PYPI_TOKEN is required for deployment.", "red"))
            raise ValueError(
                color_text("PYPI_TOKEN is required for deployment.", "red")
            )

        locked_version = get_dynamic_version(
            MANUAL_VERSION=self.target_version,
            AUTO_INCREMENT=True,
            BUMP_TYPE=self.bump_type,
        )

        # If dynamic resolution failed but a manual target version was provided,
        # prefer the explicit `target_version` (unless it's also invalid).
        if locked_version == "0.0.0":
            if self.target_version and self.target_version != "0.0.0":
                locked_version = self.target_version
            else:
                print(
                    color_text(
                        "Error: Invalid version '0.0.0'. Aborting deployment.", "red"
                    )
                )
                raise ValueError(
                    color_text("Invalid version '0.0.0'. Check pyproject.toml.", "red")
                )

        self._clean_dist()

        # Safe subprocess usage: arguments are trusted, no shell=True
        try:
            subprocess.run(
                [sys.executable, "-m", "build"],
                check=True,
                cwd=self.base_dir,  # nosec B603: args are trusted, no shell
            )
        except subprocess.CalledProcessError as err:
            print(color_text(f"Build failed: {err}. Aborting deployment.", "red"))
            raise RuntimeError("Build failed. Aborting deployment.") from err

        dist_dir = self.base_dir / "dist"
        dist_files = (
            [
                f
                for f in dist_dir.glob("*")
                if f.suffix in {".whl", ".tar.gz"} and f.is_file()
            ]
            if dist_dir.exists()
            else []
        )
        if not dist_files:
            print(
                color_text(f"Error: No distribution files found in {dist_dir}.", "red")
            )
            raise RuntimeError(
                color_text("No distribution files found. Build may have failed.", "red")
            )

        # Securely pass the token via environment variables so it
        # does not appear in process listings.
        env = os.environ.copy()
        env["TWINE_USERNAME"] = "__token__"
        env["TWINE_PASSWORD"] = self.token
        env["TWINE_REPOSITORY"] = self.repository

        cmd = [sys.executable, "-m", "twine", "upload"] + [str(f) for f in dist_files]

        try:
            subprocess.run(cmd, check=True, env=env)  # nosec B603: args are trusted
            print(
                color_text(
                    f"Deployment successful! Version {locked_version} uploaded to {self.repository}.",  # noqa: E501
                    "green",
                )
            )
        except subprocess.CalledProcessError as err:
            print(
                color_text(
                    f"Upload failed: {err}. Please check the error messages above.",
                    "red",
                )
            )
            raise RuntimeError(
                color_text(
                    "Upload failed. Please check the error messages above.", "red"
                )
            ) from err


# Expose module under test-friendly alias used by tests (allows
# patching using the 'src.pyforge_deploy.builders.pypi' path)
sys.modules.setdefault("src.pyforge_deploy.builders.pypi", sys.modules[__name__])
