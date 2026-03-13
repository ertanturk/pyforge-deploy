import os
import shutil
import subprocess  # nosec B404: subprocess usage is safe, no shell=True, trusted args
import sys
from pathlib import Path

from dotenv import load_dotenv

from pyforge_deploy.colors import (
    color_text,
    is_ci_environment,
)

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
        verbose: bool = False,
    ):
        """
        Initialize distributor.
        :param target_version: Manually set version to deploy.
        :param use_test_pypi: Deploy to TestPyPI if True, else PyPI.
        :param bump_type: Version bump type ('major', 'minor', 'patch').
        :param verbose: Enable detailed logging for debugging.
        """
        self.target_version = target_version
        self.bump_type = bump_type
        self.repository = "testpypi" if use_test_pypi else "pypi"
        self.verbose = verbose
        self.base_dir = Path.cwd()

        env_path = self.base_dir / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
            self._log(f".env file loaded from {env_path}", "blue")
        else:
            if self.verbose:
                print(
                    color_text(
                        f"Notice: .env file not found at {env_path}. Using system environment variables.",  # noqa: E501
                        "yellow",
                    )
                )
        self.token = os.environ.get("PYPI_TOKEN")

    def _log(self, message: str, color: str = "blue") -> None:
        """Helper to log messages only if verbose mode or CI is enabled."""
        if self.verbose or is_ci_environment():
            print(color_text(f"  [DEBUG] {message}", color))

    def _clean_dist(self) -> None:
        """
        Remove build artifacts, dist directory, and egg-info to prevent version caching.
        """
        self._log("Cleaning build artifacts and dist directory...")
        paths_to_clean = [
            self.base_dir / "dist",
            self.base_dir / "build",
        ]

        paths_to_clean.extend(self.base_dir.glob("*.egg-info"))
        paths_to_clean.extend((self.base_dir / "src").glob("*.egg-info"))

        for path in paths_to_clean:
            if path.exists():
                self._log(f"Removing: {path}", "yellow")
                shutil.rmtree(path) if path.is_dir() else path.unlink()

    def deploy(self) -> None:
        """
        Build and upload package to PyPI/TestPyPI.
        Handles token, version, build, upload, and logs errors.
        """
        if not self.token:
            print(color_text("Error: PYPI_TOKEN is required for deployment.", "red"))
            if is_ci_environment():
                print("::error::PYPI_TOKEN is missing in CI environment secrets.")
            raise ValueError(
                color_text("PYPI_TOKEN is required for deployment.", "red")
            )

        self._log(f"Starting deployment to {self.repository}...")

        locked_version = get_dynamic_version(
            MANUAL_VERSION=self.target_version,
            AUTO_INCREMENT=True,
            BUMP_TYPE=self.bump_type,
        )

        if locked_version == "0.0.0":
            if self.target_version and self.target_version != "0.0.0":
                locked_version = self.target_version
            else:
                error_msg = "Invalid version '0.0.0'. Check pyproject.toml."
                print(color_text(f"Error: {error_msg}", "red"))
                if is_ci_environment():
                    print(f"::error::{error_msg}")
                raise ValueError(color_text(error_msg, "red"))

        self._log(f"Resolved version for deployment: {locked_version}", "green")

        self._clean_dist()

        self._log("Running build command: python -m build")
        try:
            subprocess.run(
                [sys.executable, "-m", "build"],
                check=True,
                cwd=self.base_dir,
                capture_output=not (self.verbose or is_ci_environment()),
                text=True,
            )  # nosec B603: arguments are trusted, no shell
            if self.verbose or is_ci_environment():
                self._log("Build output detected. Proceeding to upload.")
        except subprocess.CalledProcessError as err:
            print(color_text(f"Build failed: {err}. Aborting deployment.", "red"))
            if is_ci_environment():
                print(f"::error::Build failed: {err.stderr}")
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
            error_msg = f"No distribution files found in {dist_dir}."
            print(color_text(f"Error: {error_msg}", "red"))
            raise RuntimeError(color_text(error_msg, "red"))

        self._log(f"Found {len(dist_files)} files to upload:")
        for f in dist_files:
            self._log(f"  - {f.name}")

        env = os.environ.copy()
        env["TWINE_USERNAME"] = "__token__"
        env["TWINE_PASSWORD"] = self.token
        env["TWINE_REPOSITORY"] = self.repository

        cmd = [sys.executable, "-m", "twine", "upload"] + [str(f) for f in dist_files]

        self._log(f"Uploading to {self.repository} using twine...")
        try:
            subprocess.run(cmd, check=True, env=env)  # nosec B603: arguments are trusted, no shell
            success_msg = (
                f"Deployment successful! Version {locked_version} uploaded to "
                f"{self.repository}."
            )
            print(color_text(success_msg, "green"))
        except subprocess.CalledProcessError as err:
            error_msg = f"Upload failed: {err}. Please check the error messages above."
            print(color_text(error_msg, "red"))
            if is_ci_environment():
                print(f"::error::Twine upload failed for version {locked_version}")
            raise RuntimeError(color_text(error_msg, "red")) from err


sys.modules.setdefault("src.pyforge_deploy.builders.pypi", sys.modules[__name__])
