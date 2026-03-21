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
from pyforge_deploy.config import resolve_setting
from pyforge_deploy.errors import PyPIDeployError, ValidationError
from pyforge_deploy.logutil import status_bar

from .version_engine import (
    fetch_latest_version,
    get_dynamic_version,
    get_project_details,
)


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
        auto_confirm: bool = False,
        dry_run: bool = False,
    ):
        """
        Initialize distributor.
        :param target_version: Manually set version to deploy.
        :param use_test_pypi: Deploy to TestPyPI if True, else PyPI.
        :param bump_type: Version bump type ('proud', 'default', 'shame').
        :param verbose: Enable detailed logging for debugging.
        """
        self.target_version = target_version
        self.bump_type = bump_type
        self.repository = "testpypi" if use_test_pypi else "pypi"
        self.verbose = verbose
        self.base_dir = Path.cwd()
        self.auto_confirm = auto_confirm
        self.dry_run = dry_run

        self._log(
            f"PyPIDistributor initialized with target_version={target_version}, "
            f"bump_type={bump_type}, repository={self.repository}, verbose={verbose}",
            "magenta",
        )
        self._log(f"Current working directory: {self.base_dir}", "magenta")
        env_path = self.base_dir / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
            self._log(f".env file loaded from {env_path}", "blue")
        else:
            self._log(
                (
                    f"Notice: .env file not found at {env_path}. "
                    "Using system environment variables."
                ),
                "yellow",
            )
        self._log(
            f"Environment variable PYPI_TOKEN present: {'PYPI_TOKEN' in os.environ}",
            "magenta",
        )
        # Token resolution: CLI has none here, so check pyproject then env
        self.token = resolve_setting(None, "pypi_token", env_keys=("PYPI_TOKEN",))

    def _log(self, message: str, color: str = "blue") -> None:
        """Helper to log messages only if verbose mode or CI is enabled."""
        if self.verbose or is_ci_environment():
            try:
                from pyforge_deploy.logutil import log as logutil

                logutil(
                    message,
                    level="info",
                    color=color,
                    component="PyPIDistributor",
                )
            except Exception:
                print(color_text(f"[PyPIDistributor] {message}", color))

    def _confirm(self, message: str) -> None:
        import sys as _sys

        if self.auto_confirm or is_ci_environment() or not _sys.stdin.isatty():
            return

        response = input(color_text(f"{message} [y/N]: ", "yellow")).strip().lower()
        if response not in ["y", "yes"]:
            print(color_text("Deployment cancelled by user.", "red"))
            sys.exit(0)

    def _get_oidc_token(self) -> str | None:
        """Fetches a short-lived PyPI token using GitHub OIDC."""
        req_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
        req_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")

        if not req_url or not req_token:
            return None

        self._log("Attempting to fetch OIDC token from GitHub...", "cyan")
        try:
            import json
            import urllib.request

            audience = "pypi"
            url = f"{req_url}&audience={audience}"
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {req_token}",
                    "Accept": "application/json; api-version=2.0",
                },
            )
            with urllib.request.urlopen(req) as response:  # nosec B310
                jwt_data = json.loads(response.read().decode("utf-8"))
                gh_jwt = jwt_data.get("value")

            if not gh_jwt:
                self._log("Failed to extract JWT from GitHub response.", "red")
                return None

            self._log("Exchanging GitHub JWT for PyPI API token...", "cyan")
            mint_url = (
                "https://test.pypi.org/_/oidc/github/mint-token"
                if self.repository == "testpypi"
                else "https://pypi.org/_/oidc/github/mint-token"
            )

            payload = json.dumps({"token": gh_jwt}).encode("utf-8")
            mint_req = urllib.request.Request(
                mint_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(mint_req) as mint_res:  # nosec B310
                pypi_data = json.loads(mint_res.read().decode("utf-8"))
                pypi_token = pypi_data.get("token")

            if pypi_token:
                self._log(
                    "Successfully minted short-lived PyPI token via OIDC!", "green"
                )
                return str(pypi_token)
        except Exception as e:
            self._log(f"OIDC token exchange failed: {e}", "yellow")

        return None

    def _cleanup(self) -> None:
        """Final cleanup after deployment to ensure no artifacts remain."""
        self._log("Cleaning up build artifacts...", "yellow")
        paths = [self.base_dir / "dist", self.base_dir / "build"]
        paths.extend(self.base_dir.glob("*.egg-info"))
        paths.extend((self.base_dir / "src").glob("*.egg-info"))

        for path in paths:
            if path.exists():
                self._log(f"Removing artifact: {path}", "yellow")
                shutil.rmtree(path) if path.is_dir() else path.unlink()

    # Backwards-compatible alias for older tests/code expecting `_clean_dist`
    def _clean_dist(self) -> None:  # pragma: no cover - simple alias
        """Alias kept for backward compatibility with older tests and callers."""
        self._cleanup()

    def _pre_flight_check(self, project_name: str, version: str) -> None:
        """Checks if the target version already exists on PyPI
        to prevent upload failures.
        """
        self._log(
            f"Checking if version {version} already exists on PyPI "
            f"for project '{project_name}'...",
        )
        latest = fetch_latest_version(project_name)
        if latest and latest == version:
            error_msg = (
                f"Version {version} already exists on PyPI. "
                "Aborting to prevent failure."
            )
            print(color_text(f"Error: {error_msg}", "red"))
            raise PyPIDeployError(error_msg)

    def _collect_dist_files(
        self,
        version: str,
        build_target: str,
    ) -> list[Path]:
        """Collect built artifacts for the target version.

        Args:
            version: Target package version.
            build_target: One of "wheel" or "both".

        Returns:
            Matching distribution files from dist/.
        """
        dist_dir = self.base_dir / "dist"
        if not dist_dir.exists():
            return []

        files: list[Path] = []
        for file_path in dist_dir.glob("*"):
            if not file_path.is_file():
                continue
            name = file_path.name
            if version not in name:
                continue
            if build_target == "wheel" and name.endswith(".whl"):
                files.append(file_path)
            elif build_target == "both" and (
                name.endswith(".whl") or name.endswith(".tar.gz")
            ):
                files.append(file_path)
        return sorted(files)

    def _build_distributions(self, build_target: str) -> None:
        """Build package distributions with uv/build.

        Args:
            build_target: Build mode: "wheel" or "both".

        Raises:
            PyPIDeployError: If build command fails.
        """
        use_uv = bool(shutil.which("uv")) and not (
            os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules
        )

        if use_uv:
            cmd = ["uv", "build"]
            if build_target == "wheel":
                cmd.append("--wheel")
        else:
            cmd = [sys.executable, "-m", "build"]
            if build_target == "wheel":
                cmd.append("--wheel")

        self._log(f"Build command: {' '.join(cmd)}", "cyan")
        try:
            subprocess.run(
                cmd,
                check=True,
                cwd=self.base_dir,
                capture_output=not (self.verbose or is_ci_environment()),
                text=True,
            )  # nosec B603
        except subprocess.CalledProcessError as err:
            raise PyPIDeployError("Build failed. Aborting deployment.") from err

    def deploy(self) -> None:
        """Build and upload package to PyPI/TestPyPI."""
        total_steps = 5
        status_bar(1, total_steps, "Authenticating PyPI deployment")
        self._log("Checking for PYPI_TOKEN before deployment...", "yellow")
        if not self.token and not self.dry_run:
            self.token = self._get_oidc_token()
            if self.token:
                print(
                    color_text("Using secure Passwordless Deployment (OIDC).", "green")
                )
        if not self.token and not self.dry_run:
            print(color_text("Error: PYPI_TOKEN is required for deployment.", "red"))
            self._log("PYPI_TOKEN missing from environment.", "red")
            if is_ci_environment():
                print("::error::PYPI_TOKEN is missing in CI environment secrets.")
            raise ValidationError(
                color_text("PYPI_TOKEN is required for deployment.", "red")
            )
        if self.dry_run and not self.token:
            self._log("[DRY RUN] Skipping token requirement check.", "yellow")

        self._log(f"Starting deployment to {self.repository}...", "cyan")
        status_bar(2, total_steps, "Resolving version and deployment options")

        is_ci_tag_release = bool(self.target_version) and os.environ.get(
            "GITHUB_REF", ""
        ).startswith("refs/tags/")
        write_version_cache = True
        if is_ci_tag_release:
            self._log(
                (
                    "Tag-based CI release detected; applying requested version to "
                    "local cache files for build consistency."
                ),
                "yellow",
            )

        locked_version = get_dynamic_version(
            MANUAL_VERSION=self.target_version,
            AUTO_INCREMENT=True,
            BUMP_TYPE=self.bump_type,
            DRY_RUN=self.dry_run,
            WRITE_CACHE=write_version_cache,
        )

        # Speed knobs (CLI->pyproject->env->default)
        build_target = str(
            resolve_setting(
                None,
                "pypi_build_target",
                env_keys=("PYFORGE_PYPI_BUILD_TARGET",),
                default="both",
            )
        ).lower()
        if build_target not in {"wheel", "both"}:
            build_target = "both"

        reuse_dist = bool(
            resolve_setting(
                None,
                "pypi_reuse_dist",
                env_keys=("PYFORGE_PYPI_REUSE_DIST",),
                default=False,
            )
        )
        skip_preflight = bool(
            resolve_setting(
                None,
                "pypi_skip_preflight",
                env_keys=("PYFORGE_PYPI_SKIP_PREFLIGHT",),
                default=False,
            )
        )

        if self.dry_run:
            status_bar(3, total_steps, "Skipping preflight checks (--dry-run)")
            status_bar(4, total_steps, "Skipping build preparation (--dry-run)")
            status_bar(5, total_steps, "Skipping upload (--dry-run)")
            self._log(
                (
                    "[DRY RUN] Would deploy "
                    f"version {locked_version} to {self.repository} "
                    f"(build_target={build_target}, reuse_dist={reuse_dist}, "
                    f"skip_preflight={skip_preflight})."
                ),
                "yellow",
            )
            print(
                color_text(
                    (
                        "[DRY RUN] Deployment simulation successful! "
                        f"Version {locked_version} is ready for {self.repository}."
                    ),
                    "green",
                )
            )
            return

        p_name, _ = get_project_details()
        status_bar(3, total_steps, "Running PyPI preflight checks")
        if not skip_preflight:
            self._pre_flight_check(p_name, locked_version)
        else:
            self._log("Skipping PyPI preflight check (fast mode).", "yellow")

        status_bar(4, total_steps, "Preparing distribution artifacts")
        dist_files: list[Path] = []
        if reuse_dist:
            dist_files = self._collect_dist_files(locked_version, build_target)
            if dist_files:
                self._log(
                    f"Reusing {len(dist_files)} prebuilt artifact(s) from dist/.",
                    "green",
                )

        if not dist_files:
            self._clean_dist()
            self._build_distributions(build_target)
            dist_files = self._collect_dist_files(locked_version, build_target)

        if not dist_files:
            raise PyPIDeployError("No distribution files found after build.")

        self._log(f"Found {len(dist_files)} files to upload:", "cyan")
        for f in dist_files:
            self._log(f"  - {f.name}", "cyan")

        token = self.token
        if token is None:
            raise ValidationError(
                color_text("PYPI_TOKEN is required for deployment.", "red")
            )

        env = os.environ.copy()

        env["TWINE_USERNAME"] = "__token__"
        env["TWINE_PASSWORD"] = token
        env["TWINE_REPOSITORY"] = self.repository

        env["UV_PUBLISH_TOKEN"] = token

        use_uv_publish = bool(shutil.which("uv")) and not (
            os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules
        )
        if use_uv_publish:
            self._log("Using ultra-fast 'uv publish' for deployment...", "cyan")
            cmd = ["uv", "publish"]

            if self.repository == "testpypi":
                cmd.extend(["--publish-url", "https://test.pypi.org/legacy/"])

            cmd.extend([str(f) for f in dist_files])
        else:
            self._log(f"Uploading to {self.repository} using twine...", "cyan")
            cmd = [sys.executable, "-m", "twine", "upload"] + [
                str(f) for f in dist_files
            ]

        self._log(f"Publish command: {' '.join(cmd)}", "cyan")
        self._log(
            "Publish environment configured (tokens are masked securely).",
            "cyan",
        )

        # Upload with retries/backoff to handle transient network issues
        status_bar(5, total_steps, "Uploading distribution to repository")
        retries = int(
            resolve_setting(
                None,
                "pypi_retries",
                env_keys=("PYFORGE_PYPI_RETRIES",),
                default=3,
            )
        )
        backoff = int(
            resolve_setting(
                None,
                "pypi_backoff",
                env_keys=("PYFORGE_PYPI_BACKOFF",),
                default=2,
            )
        )

        attempt = 0
        try:
            while True:
                attempt += 1
                try:
                    subprocess.run(cmd, check=True, env=env)  # nosec B603: arguments are trusted, no shell
                    success_msg = (
                        f"Deployment successful! Version {locked_version} uploaded to "
                        f"{self.repository}."
                    )
                    print(color_text(success_msg, "green"))
                    self._log(success_msg, "green")
                    break
                except subprocess.CalledProcessError as err:
                    if attempt >= retries:
                        error_msg = f"Upload failed after {retries} attempts: {err}"
                        print(color_text(error_msg, "red"))
                        if is_ci_environment():
                            print(f"::error::{error_msg}")
                        raise PyPIDeployError(color_text(error_msg, "red")) from err
                    wait = backoff**attempt
                    self._log(
                        (
                            f"Upload failed, retrying in {wait}s "
                            f"(attempt {attempt}/{retries})"
                        ),
                        "yellow",
                    )
                    import time

                    time.sleep(wait)
        except Exception as err:
            error_msg = f"Upload failed: {err}. Please check the error messages above."
            tip_msg = (
                "\nTIP: If you manually deleted this version from PyPI, "
                "you CANNOT reuse the same version number. "
                "PyPI strictly forbids reusing deleted versions. "
                "Please bump your version (e.g., --bump shame) and try again."
            )
            print(color_text(error_msg, "red"))
            print(color_text(tip_msg, "yellow"))

            if is_ci_environment():
                print(f"::error::Twine upload failed for version {locked_version}")
            raise PyPIDeployError(color_text(error_msg, "red")) from err
        finally:
            self._cleanup()


sys.modules.setdefault("src.pyforge_deploy.builders.pypi", sys.modules[__name__])
