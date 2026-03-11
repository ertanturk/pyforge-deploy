import os
import shutil
import subprocess  # nosec B404: subprocess usage is safe, no shell=True, trusted args
import sys
from pathlib import Path

from dotenv import load_dotenv

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
                f"Warning: .env file not found at {env_path}. "
                "PYPI_TOKEN may be missing."
            )
        self.token = os.environ.get("PYPI_TOKEN")

    def _clean_dist(self) -> None:
        """
        Remove dist directory if it exists.
        """
        dist_dir = self.base_dir / "dist"
        if dist_dir.exists():
            shutil.rmtree(dist_dir)

    def deploy(self) -> None:
        """
        Build and upload package to PyPI/TestPyPI.
        Handles token, version, build, upload, and logs errors.
        """
        if not self.token:
            print("Error: PYPI_TOKEN is required for deployment.")
            raise ValueError("PYPI_TOKEN is required for deployment.")

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
                print("Error: Invalid version '0.0.0'. Aborting deployment.")
                raise ValueError("Invalid version '0.0.0'. Check pyproject.toml.")

        self._clean_dist()

        # Safe subprocess usage: arguments are trusted, no shell=True
        try:
            subprocess.run(
                [sys.executable, "-m", "build"],
                check=True,
                cwd=self.base_dir,  # nosec B603: args are trusted, no shell
            )
        except subprocess.CalledProcessError as err:
            print(f"Build failed: {err}. Aborting deployment.")
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
            print(f"Error: No distribution files found in {dist_dir}.")
            raise RuntimeError("No distribution files found. Build may have failed.")

        # Safe subprocess usage: all arguments are trusted, no shell=True
        cmd = [
            sys.executable,
            "-m",
            "twine",
            "upload",
            "--repository",
            self.repository,
            "-u",
            "__token__",
            "-p",
            self.token,
        ] + [str(f) for f in dist_files]

        try:
            subprocess.run(cmd, check=True)  # nosec B603: args are trusted, no shell
            print(
                f"Deployment successful! Version {locked_version} "
                f"uploaded to {self.repository}."
            )
        except subprocess.CalledProcessError as err:
            print(f"Upload failed: {err}. Please check the error messages above.")
            raise RuntimeError(
                "Upload failed. Please check the error messages above."
            ) from err


# Expose module under test-friendly alias used by tests (allows
# patching using the 'src.pyforge_deploy.builders.pypi' path)
sys.modules.setdefault("src.pyforge_deploy.builders.pypi", sys.modules[__name__])
