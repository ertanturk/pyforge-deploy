"""Version suggestion logic for focused release flow."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEMVER_TAG_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+._]?[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)


@dataclass(slots=True)
class VersionSuggestion:
    """Suggested version metadata from commit analysis."""

    current_version: str
    suggested_version: str
    bump: str


class VersionResolver:
    """Resolve latest release and suggest next semantic version."""

    def get_latest_tag(self, tags: list[str]) -> str | None:
        """Return latest semver tag from git tags list."""
        normalized: list[tuple[int, int, int, str]] = []
        for tag in tags:
            match = _SEMVER_TAG_RE.match(tag.strip())
            if match is None:
                continue
            normalized.append(
                (
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    tag.strip(),
                )
            )
        if not normalized:
            return None
        _, _, _, latest = max(normalized)
        return latest

    def suggest_next_version(
        self,
        latest_tag: str | None,
        bumps: list[str],
        explicit_version: str | None = None,
    ) -> VersionSuggestion:
        """Suggest next version based on highest bump severity."""
        if explicit_version:
            cleaned = explicit_version.strip().lstrip("v")
            current = latest_tag.lstrip("v") if latest_tag else "0.0.0"
            return VersionSuggestion(
                current_version=current,
                suggested_version=cleaned,
                bump="explicit",
            )

        current = latest_tag.lstrip("v") if latest_tag else "0.0.0"
        major, minor, patch = self._parse(current)

        rank = {"patch": 1, "minor": 2, "major": 3}
        bump = "patch"
        for item in bumps:
            if rank.get(item, 1) > rank[bump]:
                bump = item

        if latest_tag is None:
            if bump == "major":
                return VersionSuggestion(
                    current_version=current,
                    suggested_version="1.0.0",
                    bump="major",
                )
            return VersionSuggestion(
                current_version=current,
                suggested_version="0.1.0",
                bump=bump,
            )

        if bump == "major":
            major += 1
            minor = 0
            patch = 0
        elif bump == "minor":
            minor += 1
            patch = 0
        else:
            patch += 1

        return VersionSuggestion(
            current_version=current,
            suggested_version=f"{major}.{minor}.{patch}",
            bump=bump,
        )

    def _parse(self, version: str) -> tuple[int, int, int]:
        match = _SEMVER_TAG_RE.match(version)
        if match is None:
            return 0, 0, 0
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
