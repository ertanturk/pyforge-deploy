"""Apply release artifacts and trigger publish behavior."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from pathlib import Path

from pyforge_deploy.builders.pypi import PyPIDistributor
from pyforge_deploy.builders.version_engine import (
    get_project_details,
    write_both_caches,
)
from pyforge_deploy.release.changelog_builder import ChangelogBuilder


class Publisher:
    """Finalize release files, git refs, and optional local publishing."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.git_exe = shutil.which("git")

    def publish(
        self,
        *,
        version: str,
        changelog_markdown: str,
        local_publish: bool,
        dry_run: bool,
    ) -> None:
        """Apply release changes and trigger CI or local publish."""
        if dry_run:
            return

        self._write_version(version)
        ChangelogBuilder().update_file(
            self.project_root / "CHANGELOG.md", changelog_markdown
        )
        self._git_commit_and_tag_local_only(version)

        if local_publish:
            distributor = PyPIDistributor(
                target_version=version,
                use_test_pypi=False,
                bump_type=None,
            )
            distributor.auto_confirm = True
            distributor.deploy()

        self._push_release_refs(f"v{version}")

    def _write_version(self, version: str) -> None:
        project_name, _ = get_project_details()
        write_both_caches(str(self.project_root), project_name, version)

    def _git_commit_and_tag(self, version: str) -> None:
        """Create release commit/tag locally and push refs to remote."""
        self._git_commit_and_tag_local_only(version)
        self._push_release_refs(f"v{version}")

    def _git_commit_and_tag_local_only(self, version: str) -> None:
        """Create release commit and tag locally without pushing to remote."""
        if self.git_exe is None:
            return

        tag_name = f"v{version}"
        if self._tag_exists(tag_name):
            return

        add_command = [
            self.git_exe,
            "add",
            "CHANGELOG.md",
            ".pyforge-deploy-cache/version_cache",
        ]
        subprocess.run(
            add_command,
            cwd=self.project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )  # nosec B603

        if self._has_staged_changes():
            subprocess.run(
                [self.git_exe, "commit", "-m", f"chore(release): v{version}"],
                cwd=self.project_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )  # nosec B603

        subprocess.run(
            [self.git_exe, "tag", tag_name],
            cwd=self.project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )  # nosec B603

    def _push_release_refs(self, tag_name: str) -> None:
        """Push release commit and tag refs to remote for CI/CD triggers."""
        if self.git_exe is None:
            return

        branch = self._current_branch()
        remote = self._branch_remote(branch) if branch is not None else "origin"

        if branch is not None:
            subprocess.run(
                [self.git_exe, "push", remote, branch],
                cwd=self.project_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )  # nosec B603
        subprocess.run(
            [self.git_exe, "push", remote, tag_name],
            cwd=self.project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )  # nosec B603

    def _current_branch(self) -> str | None:
        """Return the current branch name, or None when detached/unavailable."""
        if self.git_exe is None:
            return None
        result = subprocess.run(
            [self.git_exe, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )  # nosec B603
        if result.returncode != 0:
            return None
        branch = result.stdout.strip()
        if not branch or branch == "HEAD":
            return None
        return branch

    def _branch_remote(self, branch: str) -> str:
        """Return configured branch remote, defaulting to origin."""
        if self.git_exe is None:
            return "origin"
        result = subprocess.run(
            [self.git_exe, "config", "--get", f"branch.{branch}.remote"],
            cwd=self.project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )  # nosec B603
        if result.returncode != 0:
            return "origin"
        remote = result.stdout.strip()
        return remote or "origin"

    def _has_staged_changes(self) -> bool:
        """Return True when there are staged changes to commit."""
        if self.git_exe is None:
            return False
        result = subprocess.run(
            [self.git_exe, "diff", "--cached", "--quiet"],
            cwd=self.project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )  # nosec B603
        return result.returncode == 1

    def _tag_exists(self, tag_name: str) -> bool:
        """Return True when the local git tag already exists."""
        if self.git_exe is None:
            return False
        result = subprocess.run(
            [self.git_exe, "rev-parse", "--verify", f"refs/tags/{tag_name}"],
            cwd=self.project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )  # nosec B603
        return result.returncode == 0
