"""Single-command release orchestration service."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pyforge_deploy.release.changelog_builder import ChangelogBuilder
from pyforge_deploy.release.commit_analyzer import (
    Commit,
    CommitAnalysis,
    CommitAnalyzer,
)
from pyforge_deploy.release.publisher import Publisher
from pyforge_deploy.release.version_resolver import VersionResolver

_INITIAL_RELEASE_COMMIT_CAP = 50


@dataclass(slots=True)
class ReleasePlan:
    """Computed release plan presented to the user before confirmation."""

    latest_tag: str | None
    commits: list[CommitAnalysis]
    suggested_version: str
    changelog_markdown: str


class ReleaseService:
    """Orchestrate commit analysis, versioning, changelog, and publishing."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        ai_fallback: Callable[[str], str] | None = None,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.git_exe = shutil.which("git")
        self.analyzer = CommitAnalyzer(
            ai_fallback=ai_fallback,
            project_root=self.project_root,
        )
        self.version_resolver = VersionResolver()
        self.changelog_builder = ChangelogBuilder()
        self.publisher = Publisher(self.project_root)

    def plan(self, target_version: str | None = None) -> ReleasePlan:
        """Create a release plan from git history."""
        latest_tag = self._latest_tag()
        commits = self._collect_commits_since(latest_tag)
        analyses = self.analyzer.analyze(commits)
        current = self.version_resolver.suggest_next_version(
            latest_tag,
            [],
        ).current_version
        bump_decision = self.analyzer.determine_bump(
            commits,
            current_version=current,
        )
        suggestion = self.version_resolver.suggest_next_version(
            latest_tag,
            [bump_decision],
            explicit_version=target_version,
        )
        changelog = self.changelog_builder.build(suggestion.suggested_version, analyses)
        return ReleasePlan(
            latest_tag=latest_tag,
            commits=analyses,
            suggested_version=suggestion.suggested_version,
            changelog_markdown=changelog,
        )

    def apply(self, plan: ReleasePlan, *, local_publish: bool, dry_run: bool) -> None:
        """Apply the plan by writing files, creating refs, and publishing."""
        self.publisher.publish(
            version=plan.suggested_version,
            changelog_markdown=plan.changelog_markdown,
            local_publish=local_publish,
            dry_run=dry_run,
        )

    def _latest_tag(self) -> str | None:
        if self.git_exe is None:
            return None
        result = subprocess.run(
            [self.git_exe, "describe", "--tags", "--abbrev=0"],
            cwd=self.project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )  # nosec B603
        if result.returncode != 0:
            return None
        tag = result.stdout.strip()
        return tag or None

    def _collect_commits_since(self, latest_tag: str | None) -> list[Commit]:
        if self.git_exe is None:
            return []

        log_command = [self.git_exe, "log"]
        if latest_tag:
            log_command.append(f"{latest_tag}..HEAD")
        else:
            log_command.extend(["-n", str(_INITIAL_RELEASE_COMMIT_CAP), "HEAD"])

        log_command.append("--pretty=format:%H%x1f%s%x1f%b%x1f%ct%x1f%P%x1e")
        result = subprocess.run(
            log_command,
            cwd=self.project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )  # nosec B603
        if result.returncode != 0 or not result.stdout:
            return []

        commits: list[Commit] = []
        for item in result.stdout.split("\x1e"):
            chunk = item.strip()
            if not chunk:
                continue
            parts = chunk.split("\x1f")
            if len(parts) < 2:
                continue
            full_hash = parts[0].strip()
            subject = parts[1].strip()
            body = parts[2].strip() if len(parts) > 2 else ""
            timestamp_text = parts[3].strip() if len(parts) > 3 else "0"
            parent_hashes = parts[4].strip().split() if len(parts) > 4 else []
            timestamp = 0
            try:
                timestamp = int(timestamp_text)
            except ValueError:
                timestamp = 0
            changed_files = self._collect_changed_files_for_commit(full_hash)
            diff_text = self._collect_diff_for_commit(full_hash)
            commits.append(
                Commit(
                    full_hash=full_hash,
                    subject=subject,
                    body=body,
                    timestamp=timestamp,
                    parent_hashes=parent_hashes,
                    changed_files=changed_files,
                    diff_text=diff_text,
                )
            )
        return commits

    def _collect_changed_files_for_commit(self, commit_hash: str) -> list[str]:
        """Return changed file paths for a single commit hash."""
        if self.git_exe is None:
            return []
        try:
            result = subprocess.run(
                [self.git_exe, "show", "--name-only", "--format=", commit_hash],
                cwd=self.project_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )  # nosec B603
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return []
        if result.returncode != 0 or not result.stdout:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _collect_diff_for_commit(self, commit_hash: str) -> str:
        """Return unified diff content for a single commit hash."""
        if self.git_exe is None:
            return ""
        try:
            result = subprocess.run(
                [
                    self.git_exe,
                    "show",
                    "--format=",
                    "--unified=0",
                    "--text",
                    commit_hash,
                ],
                cwd=self.project_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )  # nosec B603
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout
