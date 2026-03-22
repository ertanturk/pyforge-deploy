"""Build and write clean changelog sections for releases."""

from __future__ import annotations

import re
from pathlib import Path

from pyforge_deploy.release.commit_analyzer import CommitAnalysis


class ChangelogBuilder:
    """Generate readable changelog markdown from analyzed commits."""

    def build(self, version: str, commits: list[CommitAnalysis]) -> str:
        """Return markdown section for a release version."""
        added = list(
            dict.fromkeys(c.summary for c in commits if c.commit_type == "feat")
        )
        fixed = list(
            dict.fromkeys(c.summary for c in commits if c.commit_type == "fix")
        )
        changed = list(
            dict.fromkeys(
                c.summary for c in commits if c.commit_type not in {"feat", "fix"}
            )
        )

        lines = [f"## v{version}"]
        if added:
            lines.append("- Added")
            lines.extend(f"  - {item}" for item in added)
        if fixed:
            lines.append("- Fixed")
            lines.extend(f"  - {item}" for item in fixed)
        if changed:
            lines.append("- Changed")
            lines.extend(f"  - {item}" for item in changed)
        if not commits:
            lines.append("- Changed")
            lines.append("  - Internal maintenance updates")
        return "\n".join(lines)

    def update_file(self, changelog_path: Path, section: str) -> None:
        """Insert release section at top of changelog file."""
        if not changelog_path.exists():
            changelog_path.write_text(f"# Changelog\n\n{section}\n", encoding="utf-8")
            return

        content = changelog_path.read_text(encoding="utf-8").strip()
        if not content.startswith("# Changelog"):
            content = f"# Changelog\n\n{content}" if content else "# Changelog"

        section_header = self._extract_section_header(section)
        if section_header:
            version_match = re.search(r"v?(\d+\.\d+\.\d+)", section_header)
            if version_match:
                version_str = version_match.group(1)
                if re.search(rf"(?m)^##.*{re.escape(version_str)}\b", content):
                    return
            elif re.search(
                rf"(?m)^{re.escape(section_header)}\s*$",
                content,
            ):
                return

        head, _, tail = content.partition("\n")
        body = tail.lstrip("\n")
        updated = f"{head}\n\n{section}\n\n{body}".rstrip() + "\n"
        changelog_path.write_text(updated, encoding="utf-8")

    def _extract_section_header(self, section: str) -> str | None:
        """Extract the first markdown version header from a section body."""
        for line in section.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                return stripped
        return None
