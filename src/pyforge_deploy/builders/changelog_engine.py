"""Release intelligence and automated changelog generation engine.

This module implements a 3-tier hybrid release intelligence engine:

1) AI-powered changelog generation (Gemini, BYOK)
2) Strict Conventional Commits parsing
3) Fuzzy heuristic fallback for malformed commit messages
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from pyforge_deploy.builders.version_engine import (
    calculate_next_version,
    get_dynamic_version,
    get_tool_config,
    suggest_bump_from_git,
)
from pyforge_deploy.errors import ValidationError
from pyforge_deploy.logutil import log as logutil

_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z]+)"
    r"(?:\((?P<scope>[a-zA-Z0-9_.\-/ ]+)\))?"
    r"(?P<breaking>!)?"
    r":\s+"
    r"(?P<description>.+)$"
)

_ALLOWED_TYPES = {
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
}

_SECTION_LABELS: dict[str, str] = {
    "breaking": "Breaking Changes",
    "feat": "Features",
    "fix": "Bug Fixes",
    "perf": "Performance",
    "refactor": "Refactoring",
    "docs": "Documentation",
    "chore": "Chores",
    "build": "Build System",
    "ci": "CI",
    "test": "Tests",
    "style": "Style",
    "revert": "Reverts",
}

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_BUMP_PRIORITY: dict[str, int] = {"shame": 1, "default": 2, "proud": 3}
_AI_CHUNK_SIZE = 200
_AI_MAX_WORKERS = 4
_FUZZY_FEATURE_KEYWORDS = {
    "add",
    "create",
    "implement",
    "support",
    "new",
    "eklendi",
    "yeni",
}
_FUZZY_FIX_KEYWORDS = {
    "fix",
    "resolve",
    "bug",
    "issue",
    "patch",
    "düzelttim",
    "cozuldu",
    "çözüldü",
}
_FUZZY_CHORE_KEYWORDS = {
    "update",
    "bump",
    "guncellendi",
    "güncellendi",
}
_FUZZY_REFACTOR_KEYWORDS = {
    "refactor",
    "clean",
}


def _log(message: str, level: str = "info", color: str = "blue") -> None:
    """Emit a structured log message."""
    logutil(message, level=level, color=color, component="changelog_engine")


def _sanitize(value: str) -> str:
    """Sanitize user-controlled text for markdown output.

    Removes control chars, collapses whitespace, and escapes markdown metacharacters.
    """
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    cleaned = " ".join(cleaned.split())
    escaped = re.sub(r"([*_\[\]()`])", r"\\\1", cleaned)
    return escaped.strip()


def _is_truthy(value: object) -> bool:
    """Return True for commonly truthy string values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


@dataclass(slots=True)
class ParsedCommit:
    """Normalized representation of a commit entry."""

    full_hash: str
    short_hash: str
    raw_subject: str
    commit_type: str
    scope: str | None
    description: str
    breaking: bool


@dataclass(slots=True)
class ReleasePlan:
    """Computed release intelligence output."""

    base_ref: str
    commits: list[ParsedCommit]
    next_version: str
    markdown_block: str


@dataclass(slots=True)
class AIProvider:
    """Active AI provider configuration for changelog generation."""

    name: str
    api_key: str
    base_url: str | None = None


class ChangelogEngine:
    """Generate and apply release changelog updates from git history."""

    def __init__(
        self, project_root: str | Path | None = None, *, verbose: bool = False
    ):
        self.project_root = Path(project_root or os.getcwd()).resolve()
        self.verbose = verbose
        self.git_exe = shutil.which("git")

    def _run_git(
        self,
        args: list[str],
        *,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str] | None:
        """Execute a git command safely and return CompletedProcess.

        Returns None when git is not available or command execution fails.
        """
        if not self.git_exe:
            _log(
                "git executable not found. Falling back to manual release mode.",
                "warning",
                "yellow",
            )
            return None

        try:
            return subprocess.run(
                [self.git_exe, *args],
                cwd=self.project_root,
                text=True,
                capture_output=True,
                check=check,
                timeout=20,
            )  # nosec B603
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
            _log(f"Git command failed ({' '.join(args)}): {exc}", "warning", "yellow")
            return None

    def _ensure_git_repository(self) -> bool:
        """Return True if project root is a valid git repository."""
        result = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return bool(result and result.returncode == 0 and "true" in result.stdout)

    def discover_base_ref(self) -> str | None:
        """Discover latest tag or fallback to first commit hash."""
        if not self._ensure_git_repository():
            _log(
                "Not a git repository. Skipping automatic changelog engine.",
                "warning",
                "yellow",
            )
            return None

        latest_tag = self._run_git(["describe", "--tags", "--abbrev=0"])
        if latest_tag and latest_tag.returncode == 0:
            tag = latest_tag.stdout.strip()
            if tag:
                return tag

        first_commit = self._run_git(["rev-list", "--max-parents=0", "HEAD"])
        if first_commit and first_commit.returncode == 0:
            first = first_commit.stdout.strip().splitlines()
            if first:
                return first[0]

        return None

    def extract_commits_since(self, base_ref: str) -> list[tuple[str, str, str]]:
        """Extract commits from base ref to HEAD.

        Uses format %H|%s|%b and reconstructs multiline bodies safely.
        """
        result = self._run_git(["log", f"{base_ref}..HEAD", "--format=%H|%s|%b"])
        if not result or result.returncode != 0:
            return []

        raw = result.stdout
        if not raw.strip():
            return []

        commits: list[tuple[str, str, str]] = []
        current_hash = ""
        current_subject = ""
        current_body_lines: list[str] = []

        for line in raw.splitlines():
            if re.match(r"^[0-9a-f]{40}\|", line):
                if current_hash:
                    commits.append(
                        (
                            current_hash,
                            current_subject,
                            "\n".join(current_body_lines).strip(),
                        )
                    )
                parts = line.split("|", 2)
                current_hash = parts[0]
                current_subject = parts[1] if len(parts) > 1 else ""
                body = parts[2] if len(parts) > 2 else ""
                current_body_lines = [body] if body else []
            else:
                current_body_lines.append(line)

        if current_hash:
            commits.append(
                (current_hash, current_subject, "\n".join(current_body_lines).strip())
            )

        return commits

    def parse_commits(self, commits: list[tuple[str, str, str]]) -> list[ParsedCommit]:
        """Parse commit tuples using strict+fuzzy hybrid categorization.

        Tier-2 strict Conventional Commit parsing is attempted first.
        Tier-3 fuzzy heuristics are applied when strict parsing fails.
        """
        if not commits:
            return []

        worker_count = min(_AI_MAX_WORKERS, max(1, len(commits)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            parsed = list(executor.map(self._parse_single_commit, commits))
        return parsed

    def _parse_single_commit(self, item: tuple[str, str, str]) -> ParsedCommit:
        """Parse a single commit tuple into ``ParsedCommit``."""
        commit_hash, subject, body = item
        safe_subject = _sanitize(subject)
        safe_body = _sanitize(body)
        match = _CONVENTIONAL_RE.match(subject.strip())
        if not match:
            fuzzy_type = self._fuzzy_categorize_commit(subject)
            return ParsedCommit(
                full_hash=commit_hash,
                short_hash=commit_hash[:7],
                raw_subject=safe_subject,
                commit_type=fuzzy_type,
                scope=None,
                description=safe_subject,
                breaking=False,
            )

        commit_type = match.group("type").lower()
        if commit_type not in _ALLOWED_TYPES:
            fuzzy_type = self._fuzzy_categorize_commit(subject)
            return ParsedCommit(
                full_hash=commit_hash,
                short_hash=commit_hash[:7],
                raw_subject=safe_subject,
                commit_type=fuzzy_type,
                scope=None,
                description=safe_subject,
                breaking=False,
            )

        scope = match.group("scope")
        description = match.group("description") or safe_subject
        is_breaking = bool(match.group("breaking")) or (
            "BREAKING CHANGE" in body.upper()
        )
        if safe_body and self.verbose:
            _log(
                (
                    f"Commit {commit_hash[:7]} includes body details "
                    "used for breaking-change checks."
                ),
                "debug",
                "gray",
            )
        return ParsedCommit(
            full_hash=commit_hash,
            short_hash=commit_hash[:7],
            raw_subject=safe_subject,
            commit_type=commit_type,
            scope=_sanitize(scope) if scope else None,
            description=_sanitize(description),
            breaking=is_breaking,
        )

    def _is_strict_conventional(self, subject: str) -> bool:
        """Return ``True`` if subject follows allowed Conventional Commit format."""
        match = _CONVENTIONAL_RE.match(subject.strip())
        if not match:
            return False
        commit_type = match.group("type").lower()
        return commit_type in _ALLOWED_TYPES

    def _select_ai_provider(self) -> AIProvider | None:
        """Select AI provider in preference order.

        Order: OpenAI -> Anthropic -> Gemini.
        """
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            openai_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            return AIProvider(name="openai", api_key=openai_key, base_url=openai_base)

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if anthropic_key:
            return AIProvider(name="anthropic", api_key=anthropic_key)

        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gemini_key:
            return AIProvider(name="gemini", api_key=gemini_key)

        return None

    def _get_custom_prompt(self) -> str | None:
        """Load optional custom prompt from [tool.pyforge-deploy.changelog]."""
        try:
            tool_config = get_tool_config()
        except Exception:
            return None

        changelog_config = tool_config.get("changelog")
        if not isinstance(changelog_config, dict):
            return None

        prompt = changelog_config.get("custom_prompt")
        if isinstance(prompt, str):
            cleaned = prompt.strip()
            return cleaned or None
        return None

    def _build_ai_prompt(
        self,
        raw_commits: list[tuple[str, str, str]],
        version: str,
        *,
        include_release_header: bool,
        chunk_index: int,
        chunk_total: int,
    ) -> str:
        """Build provider-agnostic AI prompt with optional custom override."""
        today = date.today().strftime("%Y-%m-%d")
        commit_lines = [
            f"- {commit_hash[:7]} | {subject} | {body}"
            for commit_hash, subject, body in raw_commits
        ]
        commits_payload = "\n".join(commit_lines)
        custom_prompt = self._get_custom_prompt()

        base_prompt = (
            custom_prompt
            if custom_prompt
            else (
                "You are a release-notes assistant. "
                "Categorize commits in Markdown using only these sections: "
                "Features, Bug Fixes, Maintenance."
            )
        )
        header_directive = (
            f"Required title format: ## [v{version}] - {today}."
            if include_release_header
            else "Do NOT include a top-level release title."
        )

        return (
            f"{base_prompt}\n"
            f"{header_directive}\n"
            "Use bullet points and include short hash in parentheses. "
            "Return only Markdown.\n"
            f"Chunk {chunk_index + 1}/{chunk_total}.\n\n"
            "Commits:\n"
            f"{commits_payload}"
        )

    def _send_ai_request(self, provider: AIProvider, prompt: str) -> str | None:
        """Send prompt to active provider endpoint and return markdown text."""
        request: urllib_request.Request
        if provider.name == "openai":
            base_url = (provider.base_url or "https://api.openai.com/v1").rstrip("/")
            url = f"{base_url}/chat/completions"
            payload = {
                "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
            request = urllib_request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {provider.api_key}",
                },
                method="POST",
            )
        elif provider.name == "anthropic":
            payload = {
                "model": os.environ.get(
                    "ANTHROPIC_MODEL",
                    "claude-3-5-haiku-latest",
                ),
                "max_tokens": 2048,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}],
            }
            request = urllib_request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": provider.api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
        else:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.5-flash:generateContent?"
                + urllib_parse.urlencode({"key": provider.api_key})
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2},
            }
            request = urllib_request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

        with urllib_request.urlopen(request, timeout=15) as response:  # nosec B310
            response_body = response.read().decode("utf-8")
        parsed_payload = json.loads(response_body)

        if provider.name == "openai":
            choices = parsed_payload.get("choices", [])
            if not choices:
                return None
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if isinstance(content, list):
                parts = [
                    item.get("text", "") for item in content if isinstance(item, dict)
                ]
                return "\n".join(parts).strip() or None
            if isinstance(content, str):
                return content.strip() or None
            return None

        if provider.name == "anthropic":
            content_blocks = parsed_payload.get("content", [])
            parts = [
                block.get("text", "")
                for block in content_blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = "\n".join(parts).strip()
            return content or None

        candidates = parsed_payload.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        content = "\n".join(texts).strip()
        return content or None

    def _chunk_commits(
        self,
        commits: list[tuple[str, str, str]],
        chunk_size: int = _AI_CHUNK_SIZE,
    ) -> list[list[tuple[str, str, str]]]:
        """Split commit list into fixed-size chunks for context-window safety."""
        if not commits:
            return []
        return [commits[i : i + chunk_size] for i in range(0, len(commits), chunk_size)]

    def _merge_ai_markdown_chunks(
        self,
        markdown_chunks: list[str],
        version: str,
    ) -> str:
        """Merge AI markdown chunk outputs into a single release block."""
        today = date.today().strftime("%Y-%m-%d")
        cleaned_bodies: list[str] = []
        header_pattern = re.compile(r"^##\s+\[v[^\]]+\]\s+-\s+\d{4}-\d{2}-\d{2}\s*$")

        for chunk in markdown_chunks:
            if not chunk:
                continue
            lines = chunk.strip().splitlines()
            if lines and header_pattern.match(lines[0].strip()):
                body = "\n".join(lines[1:]).strip()
            else:
                body = chunk.strip()
            if body:
                cleaned_bodies.append(body)

        merged_body = "\n\n".join(cleaned_bodies).strip()
        header = f"## [v{version}] - {today}"
        if not merged_body:
            return header
        return f"{header}\n\n{merged_body}"

    def _merge_local_and_ai_markdown(
        self,
        local_markdown: str,
        ai_markdown: str,
        version: str,
    ) -> str:
        """Merge local strict markdown and AI messy markdown under one header."""
        today = date.today().strftime("%Y-%m-%d")
        header = f"## [v{version}] - {today}"
        header_pattern = re.compile(r"^##\s+\[v[^\]]+\]\s+-\s+\d{4}-\d{2}-\d{2}\s*$")

        def strip_header(value: str) -> str:
            lines = value.strip().splitlines()
            if lines and header_pattern.match(lines[0].strip()):
                return "\n".join(lines[1:]).strip()
            return value.strip()

        local_body = strip_header(local_markdown)
        ai_body = strip_header(ai_markdown)
        body_parts = [part for part in [local_body, ai_body] if part]
        if not body_parts:
            return header
        return f"{header}\n\n" + "\n\n".join(body_parts)

    def _fuzzy_categorize_commit(self, subject: str) -> str:
        """Categorize malformed commit messages using keyword heuristics.

        Returns one of: ``feat``, ``fix``, ``chore``, ``refactor``, or ``misc``.
        """
        normalized = subject.casefold()
        ascii_normalized = (
            normalized.replace("ç", "c")
            .replace("ö", "o")
            .replace("ü", "u")
            .replace("ğ", "g")
            .replace("ı", "i")
            .replace("ş", "s")
        )
        tokens = set(re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]+", normalized))
        ascii_tokens = set(re.findall(r"[a-zA-Z]+", ascii_normalized))

        if tokens & _FUZZY_FEATURE_KEYWORDS or ascii_tokens & _FUZZY_FEATURE_KEYWORDS:
            return "feat"
        if tokens & _FUZZY_FIX_KEYWORDS or ascii_tokens & _FUZZY_FIX_KEYWORDS:
            return "fix"
        if tokens & _FUZZY_REFACTOR_KEYWORDS or ascii_tokens & _FUZZY_REFACTOR_KEYWORDS:
            return "refactor"
        if tokens & _FUZZY_CHORE_KEYWORDS or ascii_tokens & _FUZZY_CHORE_KEYWORDS:
            return "chore"
        return "misc"

    def _generate_changelog_via_ai(
        self,
        raw_commits: list[tuple[str, str, str]],
        version: str,
    ) -> str | None:
        """Generate markdown changelog using routed provider with safe fallback.

        Returns ``None`` when no provider is configured or request fails,
        allowing local Tier-2/Tier-3 fallback logic to continue.
        """
        provider = self._select_ai_provider()
        if provider is None:
            return None

        if not raw_commits:
            return None

        chunks = self._chunk_commits(raw_commits)
        chunk_count = len(chunks)

        try:
            if chunk_count == 1:
                prompt = self._build_ai_prompt(
                    chunks[0],
                    version,
                    include_release_header=True,
                    chunk_index=0,
                    chunk_total=1,
                )
                markdown = self._send_ai_request(provider, prompt)
                return markdown

            worker_count = min(_AI_MAX_WORKERS, chunk_count)

            def run_chunk(index: int) -> tuple[int, str | None]:
                prompt = self._build_ai_prompt(
                    chunks[index],
                    version,
                    include_release_header=False,
                    chunk_index=index,
                    chunk_total=chunk_count,
                )
                return index, self._send_ai_request(provider, prompt)

            indexed_outputs: list[tuple[int, str | None]] = []
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                indexed_outputs = list(executor.map(run_chunk, range(chunk_count)))

            ordered_outputs = [
                value for _, value in sorted(indexed_outputs, key=lambda item: item[0])
            ]
            markdown_chunks = [
                value for value in ordered_outputs if isinstance(value, str)
            ]
            if not markdown_chunks:
                return None
            return self._merge_ai_markdown_chunks(markdown_chunks, version)
        except (
            TimeoutError,
            urllib_error.URLError,
            ValueError,
            KeyError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            _log(
                (
                    "Gemini changelog generation failed; "
                    f"falling back to local engine: {exc}"
                ),
                "warning",
                "yellow",
            )
            return None

    def decide_bump(self, commits: list[ParsedCommit]) -> str:
        """Determine release bump mode using the project's shared system.

        Returns one of Pride modes: `proud`, `default`, `shame`.
        """
        if any(c.breaking for c in commits):
            parsed_bump = "proud"
        elif any(c.commit_type == "feat" for c in commits):
            parsed_bump = "default"
        elif any(c.commit_type in {"fix", "perf", "refactor"} for c in commits):
            parsed_bump = "shame"
        else:
            parsed_bump = "shame"

        git_bump = suggest_bump_from_git()
        parsed_rank = _BUMP_PRIORITY.get(parsed_bump, 1)
        git_rank = _BUMP_PRIORITY.get(git_bump, 1)
        selected = parsed_bump if parsed_rank >= git_rank else git_bump
        if self.verbose:
            _log(
                (
                    "Selected bump mode using parsed commits and git history: "
                    f"parsed={parsed_bump}, git={git_bump}, selected={selected}"
                ),
                "debug",
                "gray",
            )
        return selected

    def _resolve_next_version(
        self,
        bump_mode: str,
        target_version: str | None,
    ) -> str:
        """Resolve next version with the current shared version engine logic."""
        if target_version:
            normalized = target_version.strip()
            if normalized.lower().startswith("v"):
                normalized = normalized[1:]
            return normalized
        current_version = get_dynamic_version(WRITE_CACHE=False)
        return calculate_next_version(current_version, bump_mode)

    def _release_tag(self, version: str) -> str:
        """Return canonical release tag in ``v{version}`` format."""
        normalized = version.strip()
        if normalized.lower().startswith("v"):
            normalized = normalized[1:]
        return f"v{normalized}"

    def _read_current_version(self, base_ref: str | None) -> str:
        """Infer current release version from tag, fallback to 0.0.0."""
        if not base_ref:
            return "0.0.0"
        match = _VERSION_RE.match(base_ref.strip())
        if not match:
            return "0.0.0"
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"

    def _increment_version(self, version: str, bump: str) -> str:
        """Increment x.y.z version deterministically.

        Kept for backward compatibility with older direct callers.
        """
        matched = _VERSION_RE.match(version)
        if not matched:
            major, minor, patch = 0, 0, 0
        else:
            major, minor, patch = (
                int(matched.group(1)),
                int(matched.group(2)),
                int(matched.group(3)),
            )

        if bump in {"major", "proud"}:
            major += 1
            minor = 0
            patch = 0
        elif bump in {"minor", "default"}:
            minor += 1
            patch = 0
        else:
            patch += 1
        return f"{major}.{minor}.{patch}"

    def build_markdown(self, version: str, commits: list[ParsedCommit]) -> str:
        """Build structured markdown release section."""
        today = date.today().strftime("%Y-%m-%d")
        lines: list[str] = [f"## [v{version}] - {today}"]

        grouped: dict[str, list[ParsedCommit]] = {}
        misc: list[ParsedCommit] = []
        breaking: list[ParsedCommit] = []
        for item in commits:
            if item.breaking:
                breaking.append(item)
            if item.commit_type == "misc":
                misc.append(item)
                continue
            grouped.setdefault(item.commit_type, []).append(item)

        if breaking:
            lines.append(f"### {_SECTION_LABELS['breaking']}")
            for commit in breaking:
                scope_prefix = f"**{commit.scope}:** " if commit.scope else ""
                lines.append(
                    f"* {scope_prefix}{commit.description} ({commit.short_hash})"
                )

        ordered_types = [
            "feat",
            "fix",
            "perf",
            "refactor",
            "docs",
            "chore",
            "build",
            "ci",
            "test",
            "style",
            "revert",
        ]

        for commit_type in ordered_types:
            if commit_type not in grouped:
                continue
            lines.append(f"### {_SECTION_LABELS[commit_type]}")
            for commit in grouped[commit_type]:
                scope_prefix = f"**{commit.scope}:** " if commit.scope else ""
                breaking_suffix = " ⚠ BREAKING" if commit.breaking else ""
                lines.append(
                    f"* {scope_prefix}{commit.description} "
                    f"({commit.short_hash}){breaking_suffix}"
                )

        if misc:
            lines.append("### Other Changes")
            for commit in misc:
                text = commit.description or commit.raw_subject
                lines.append(f"* {text} ({commit.short_hash})")

        lines.append("")
        return "\n".join(lines)

    def _merge_changelog(self, markdown_block: str, changelog_path: Path) -> str:
        """Merge new markdown section under '# Changelog' without data loss."""
        if not changelog_path.exists():
            return f"# Changelog\n\n{markdown_block}\n"

        content = changelog_path.read_text(encoding="utf-8")
        header_pattern = re.compile(r"^#\s+Changelog\s*$", flags=re.MULTILINE)
        match = header_pattern.search(content)
        if not match:
            return f"# Changelog\n\n{markdown_block}\n{content.lstrip()}"

        insert_at = match.end()
        prefix = content[:insert_at].rstrip() + "\n\n"
        suffix = content[insert_at:].lstrip("\n")
        return f"{prefix}{markdown_block}\n{suffix}"

    def _assert_clean_tree(self) -> None:
        """Validate working tree cleanliness before release git operations."""
        result = self._run_git(["status", "--porcelain"])
        if not result or result.returncode != 0:
            return

        allowed_files = {
            "CHANGELOG.md",
            ".version_cache",
            ".pyforge-deploy-cache/version_cache",
            "src/pyforge_deploy/__about__.py",
        }
        disallowed: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            normalized = path.replace("\\", "/")
            if normalized not in allowed_files:
                disallowed.append(normalized)

        if disallowed:
            sample = ", ".join(disallowed[:5])
            raise ValidationError(
                "Working tree is dirty. Commit or stash non-release changes "
                f"before running release automation. Found: {sample}"
            )

    def _run_release_git_ops(self, version: str) -> None:
        """Execute release git operations for changelog commit and tag."""
        release_tag = self._release_tag(version)
        branch_result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if not branch_result or branch_result.returncode != 0:
            details = (
                branch_result.stderr.strip()
                if branch_result and branch_result.stderr
                else "unable to resolve current branch"
            )
            raise ValidationError(f"Release git operation failed: {details}")

        branch_name = branch_result.stdout.strip()
        if not branch_name or branch_name == "HEAD":
            raise ValidationError(
                "Release git operation failed: detached HEAD is not supported for "
                "release push operations"
            )

        remote_result = self._run_git(
            ["config", "--get", f"branch.{branch_name}.remote"]
        )
        remote_name = (
            remote_result.stdout.strip()
            if remote_result and remote_result.returncode == 0 and remote_result.stdout
            else "origin"
        )

        operations = [
            ["add", "CHANGELOG.md"],
            ["commit", "-m", f"chore(release): {release_tag}"],
            ["tag", release_tag],
            ["push", remote_name, branch_name],
            ["push", remote_name, release_tag],
        ]
        for op in operations:
            result = self._run_git(op)
            if not result or result.returncode != 0:
                details = result.stderr.strip() if result else "git command unavailable"
                raise ValidationError(
                    f"Release git operation failed: git {' '.join(op)} :: {details}"
                )

        verify_result = self._run_git(
            ["ls-remote", "--tags", remote_name, f"refs/tags/{release_tag}"]
        )
        if (
            not verify_result
            or verify_result.returncode != 0
            or not verify_result.stdout.strip()
        ):
            raise ValidationError(
                "Release git operation failed: remote tag verification failed for "
                f"{remote_name}/{release_tag}"
            )

    def finalize_release_git_ops(
        self, version: str, *, allow_dirty: bool = False
    ) -> None:
        """Finalize release by committing changelog, tagging and pushing.

        Args:
            version: Release version (without or with leading ``v``).
            allow_dirty: When True, bypass clean-tree check.
        """
        if not allow_dirty:
            self._assert_clean_tree()
        else:
            _log(
                (
                    "Dirty-tree check bypassed via allow_dirty override. "
                    "Proceeding with release git operations."
                ),
                "warning",
                "yellow",
            )
        self._run_release_git_ops(version)

    def plan_release(self, target_version: str | None = None) -> ReleasePlan | None:
        """Compute release plan via AI-first waterfall with robust fallback.

        Waterfall:
        1) Tier-1 AI markdown generation (Gemini BYOK)
        2) Tier-2 strict conventional commit parsing
        3) Tier-3 fuzzy heuristic categorization
        """
        base_ref = self.discover_base_ref()
        if not base_ref:
            return None

        raw_commits = self.extract_commits_since(base_ref)
        if not raw_commits:
            _log(
                (
                    "[INFO] No new commits found since the last release. "
                    "Skipping deployment."
                ),
                "info",
                "cyan",
            )
            return None

        parsed = self.parse_commits(raw_commits)
        bump = self.decide_bump(parsed)
        next_version = self._resolve_next_version(bump, target_version)

        strict_hashes = {
            commit_hash
            for commit_hash, subject, _body in raw_commits
            if self._is_strict_conventional(subject)
        }
        malformed_commits = [
            item for item in raw_commits if item[0] not in strict_hashes
        ]

        ai_markdown = self._generate_changelog_via_ai(malformed_commits, next_version)
        if ai_markdown is not None and malformed_commits:
            strict_parsed = [c for c in parsed if c.full_hash in strict_hashes]
            local_markdown = self.build_markdown(next_version, strict_parsed)
            merged_markdown = self._merge_local_and_ai_markdown(
                local_markdown,
                ai_markdown,
                next_version,
            )
            if self.verbose:
                _log(
                    (
                        "Tier-1 AI changelog generated for malformed commits; "
                        "merged with strict local parsing output."
                    ),
                    "debug",
                    "gray",
                )
            return ReleasePlan(
                base_ref=base_ref,
                commits=parsed,
                next_version=next_version,
                markdown_block=merged_markdown,
            )

        markdown = self.build_markdown(next_version, parsed)
        return ReleasePlan(
            base_ref=base_ref,
            commits=parsed,
            next_version=next_version,
            markdown_block=markdown,
        )

    def execute(
        self,
        *,
        dry_run: bool = False,
        target_version: str | None = None,
        allow_dirty: bool = False,
        apply_git_ops: bool = True,
    ) -> ReleasePlan | None:
        """Execute full release intelligence lifecycle.

        In dry-run mode, prints generated markdown and performs no writes or git ops.
        """
        plan = self.plan_release(target_version=target_version)
        if not plan:
            return None

        if dry_run:
            _log(
                "Dry-run mode active. Displaying generated changelog only.",
                "info",
                "yellow",
            )
            print(plan.markdown_block)
            return plan

        changelog_path = self.project_root / "CHANGELOG.md"
        merged = self._merge_changelog(plan.markdown_block, changelog_path)
        changelog_path.write_text(merged, encoding="utf-8")
        _log(f"Updated changelog at {changelog_path}", "info", "green")
        if apply_git_ops:
            self.finalize_release_git_ops(plan.next_version, allow_dirty=allow_dirty)
            _log(
                f"Release intelligence completed for v{plan.next_version}",
                "info",
                "green",
            )
        else:
            _log(
                (
                    "Release intelligence completed changelog phase for "
                    f"v{plan.next_version}; git ops pending."
                ),
                "info",
                "green",
            )
        return plan


def run_release_intelligence(
    *,
    project_root: str | Path | None = None,
    dry_run: bool = False,
    target_version: str | None = None,
    verbose: bool = False,
    allow_dirty: bool = False,
    apply_git_ops: bool = True,
) -> ReleasePlan | None:
    """Convenience wrapper to execute changelog lifecycle."""
    engine = ChangelogEngine(project_root=project_root, verbose=verbose)
    return engine.execute(
        dry_run=dry_run,
        target_version=target_version,
        allow_dirty=allow_dirty,
        apply_git_ops=apply_git_ops,
    )
