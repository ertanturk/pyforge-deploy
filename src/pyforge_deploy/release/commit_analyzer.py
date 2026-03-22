"""Commit parsing and bump classification for release planning."""

from __future__ import annotations

import ast
import math
import os
import re
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pyforge_deploy.logutil import log

_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s+(?P<desc>.+)$"
)
_CLEANUP_REMOVE_PATTERNS = (
    r"remove\s+unused\s+imports?",
    r"remove\s+dead\s+code",
    r"remove\s+trailing\s+whitespace",
    r"remove\s+console\.?log",
    r"remove\s+debug\s+logs?",
    r"remove\s+print\s+statements?",
    r"remove\s+lint\s+noise",
    r"remove\s+format(?:ting)?\s+noise",
)
_DEPRECATION_PATTERNS = (
    re.compile(r"^\+\s*@deprecated\b", re.IGNORECASE),
    re.compile(r"^\+.*DeprecationWarning\b"),
    re.compile(r"^\+\s*warnings\.warn\(.*deprecat", re.IGNORECASE),
)
_SECURITY_OVERRIDE_RE = re.compile(
    r"\b(cve-\d{4}-\d+|ghsa-[a-z0-9\-]+|security vulnerability|zero-day|hotfix)\b",
    re.IGNORECASE,
)
_SCHEMA_DESTRUCTIVE_PATTERNS = (
    r"DROP\s+TABLE",
    r"ALTER\s+TABLE\s+.*DROP\s+COLUMN",
    r"op\.drop_column",
    r"op\.drop_table",
    r"migrations\.RemoveField",
)


@dataclass(slots=True)
class Commit:
    """Raw git commit data used by release planning."""

    full_hash: str
    subject: str
    body: str
    timestamp: int = 0
    parent_hashes: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    diff_text: str = ""


@dataclass(slots=True)
class CommitAnalysis:
    """Normalized commit analysis output for UX and version resolution."""

    full_hash: str
    original_subject: str
    summary: str
    commit_type: str
    bump: str
    source: str


@dataclass(slots=True)
class ScoreMatrix:
    """Weighted impact matrix for semantic version decisions."""

    major: float = 0.0
    minor: float = 0.0
    patch: float = 0.0

    def add(self, other: ScoreMatrix) -> ScoreMatrix:
        """Return a new matrix that is the sum of two score matrices."""
        return ScoreMatrix(
            major=self.major + other.major,
            minor=self.minor + other.minor,
            patch=self.patch + other.patch,
        )

    def scaled(self, multiplier: float) -> ScoreMatrix:
        """Return a new matrix scaled by a multiplier."""
        safe = max(multiplier, 0.0)
        return ScoreMatrix(
            major=self.major * safe,
            minor=self.minor * safe,
            patch=self.patch * safe,
        )

    def values(self) -> tuple[float, float, float]:
        """Return matrix values as (major, minor, patch)."""
        return (self.major, self.minor, self.patch)

    def total(self) -> float:
        """Return total impact score across all dimensions."""
        return self.major + self.minor + self.patch

    def confidence(self) -> float:
        """Return confidence score based on dominant signal share."""
        total = self.total()
        if total <= 0.0:
            return 0.0
        return max(self.values()) / total

    def dominant(self) -> str:
        """Return dominant semantic bump label by score."""
        ranking = {
            "major": self.major,
            "minor": self.minor,
            "patch": self.patch,
        }
        return max(ranking.items(), key=lambda item: item[1])[0]


class CommitAnalyzer:
    """Analyze commits with conventional, heuristic, and optional AI fallback."""

    def __init__(
        self,
        ai_fallback: Callable[[str], str] | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.ai_fallback = ai_fallback
        self.project_root = Path(project_root).resolve() if project_root else None
        self.git_exe = shutil.which("git")

    def analyze(self, commits: list[Commit]) -> list[CommitAnalysis]:
        """Analyze commit list and classify bump impact."""
        analyses: list[CommitAnalysis] = []
        for commit in commits:
            conventional = self._parse_conventional(commit)
            if conventional is not None:
                analyses.append(conventional)
                continue

            heuristic = self._parse_heuristic(commit)
            if heuristic is not None:
                analyses.append(heuristic)
                continue

            summary = commit.subject.strip()
            if self.ai_fallback is not None:
                try:
                    summary = self.ai_fallback(commit.subject).strip() or summary
                except Exception:
                    summary = commit.subject.strip()

            analyses.append(
                CommitAnalysis(
                    full_hash=commit.full_hash,
                    original_subject=commit.subject,
                    summary=summary,
                    commit_type="chore",
                    bump="patch",
                    source="ai",
                )
            )
        return analyses

    def determine_bump(
        self,
        commits: list[Commit],
        *,
        current_version: str = "0.0.0",
    ) -> str:
        """Determine semantic bump using weighted multi-layer heuristics."""
        if not commits:
            return "patch"

        filtered_commits = self._filter_noise(commits)
        if not filtered_commits:
            return "patch"

        global_signal = self._aggregate_signal(filtered_commits)
        decision = self._decision_from_signal(
            global_signal,
            current_version=current_version,
        )
        confidence = global_signal.confidence()

        if confidence > 0.75:
            return decision

        if 0.5 <= confidence <= 0.75:
            log(
                (
                    "Release bump confidence is moderate; "
                    f"major={global_signal.major:.2f}, "
                    f"minor={global_signal.minor:.2f}, "
                    f"patch={global_signal.patch:.2f}, "
                    f"confidence={confidence:.2f}, decision={decision}"
                ),
                level="warning",
                color="yellow",
                component="release",
            )
            return decision

        print("\nRelease bump confidence is LOW.")
        print("Signal breakdown:")
        print(f"  major: {global_signal.major:.3f}")
        print(f"  minor: {global_signal.minor:.3f}")
        print(f"  patch: {global_signal.patch:.3f}")
        print(f"  confidence: {confidence:.3f}")

        ai_signal = self._ai_assisted_evaluation(filtered_commits)
        ai_matrix = ScoreMatrix(
            major=float(ai_signal.get("major", 0.0)),
            minor=float(ai_signal.get("minor", 0.0)),
            patch=float(ai_signal.get("patch", 0.0)),
        )
        ai_decision = ai_matrix.dominant()
        print(f"AI fallback suggests: {ai_decision.upper()}")

        is_ci = (
            os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        )
        if is_ci or not sys.stdin.isatty():
            return ai_decision

        prompt = (
            f"Override bump [major/minor/patch] or press Enter to confirm "
            f"{ai_decision}: "
        )
        try:
            user_choice = input(prompt).strip().lower()
        except EOFError:
            user_choice = ""

        if user_choice in {"major", "minor", "patch"}:
            return user_choice
        return ai_decision

    def _parse_conventional(self, commit: Commit) -> CommitAnalysis | None:
        subject = commit.subject.strip()
        match = _CONVENTIONAL_RE.match(subject)
        if match is None:
            return None

        commit_type = match.group("type").lower()
        description = (match.group("desc") or subject).strip()
        breaking = bool(match.group("breaking")) or "BREAKING CHANGE" in commit.body

        if breaking:
            bump = "major"
        elif commit_type == "feat":
            bump = "minor"
        else:
            bump = "patch"

        return CommitAnalysis(
            full_hash=commit.full_hash,
            original_subject=commit.subject,
            summary=description,
            commit_type=commit_type,
            bump=bump,
            source="conventional",
        )

    def _filter_noise(self, commits: list[Commit]) -> list[Commit]:
        """Filter merge/trivial commits before numerical scoring."""
        filtered: list[Commit] = []
        for commit in commits:
            subject = commit.subject.strip().casefold()
            if subject.startswith("chore(release):"):
                continue
            if len(commit.parent_hashes) > 1 or subject.startswith("merge"):
                continue
            if re.search(r"\b(wip|typo|minor\s+changes?)\b", subject):
                continue
            filtered.append(commit)
        return filtered

    def _aggregate_signal(self, commits: list[Commit]) -> ScoreMatrix:
        """Aggregate per-commit score matrices into a global signal."""
        ordered = sorted(commits, key=lambda item: item.timestamp or 0)
        global_signal = ScoreMatrix()
        previous_ts = 0
        for commit in ordered:
            time_delta = 0
            if previous_ts and commit.timestamp:
                time_delta = max(commit.timestamp - previous_ts, 0)
            previous_ts = commit.timestamp or previous_ts
            commit_signal = self._score_commit(commit, time_delta)
            global_signal = global_signal.add(commit_signal)
        return global_signal

    def _score_commit(self, commit: Commit, time_delta_seconds: int) -> ScoreMatrix:
        """Compute score matrix for one commit across all heuristic layers."""
        if self._is_revert_commit(commit):
            return ScoreMatrix(patch=1.0)

        signal = self._message_signal(commit)
        signal = signal.add(self._change_density_signal(commit))
        signal = signal.add(self._structural_signal(commit))
        signal = signal.add(self._dependency_signal(commit))
        signal = signal.add(self._deprecation_signal(commit))
        signal = signal.add(self._schema_migration_signal(commit))

        blast_weight = self._blast_radius_weight(commit.changed_files)
        signal = signal.scaled(blast_weight)

        if commit.changed_files and self._only_docs_or_tests(commit.changed_files):
            signal = signal.scaled(0.2)

        signal = signal.scaled(self._test_impact_ratio_multiplier(commit.changed_files))

        files_changed = len(commit.changed_files)
        logical_changed, _ = self._logical_diff_stats(commit.diff_text)
        if time_delta_seconds >= 600 and files_changed >= 3 and logical_changed >= 5:
            churn_multiplier = math.log(time_delta_seconds + 1.0) * files_changed
            bounded = min(churn_multiplier, 8.0)
            signal = signal.scaled(bounded)

        return signal

    def _message_signal(self, commit: Commit) -> ScoreMatrix:
        """Score commit intent from message verbs and semantic hints."""
        text = f"{commit.subject} {commit.body}".casefold()
        if _SECURITY_OVERRIDE_RE.search(text) is not None:
            return ScoreMatrix(major=0.0, minor=0.0, patch=100.0)

        patch_terms = ("fix", "resolve", "patch")
        minor_terms = ("feat", "add", "implement")
        major_terms = ("breaking", "drop", "rewrite")

        patch_score = float(sum(1 for term in patch_terms if term in text))
        minor_score = float(sum(1 for term in minor_terms if term in text))
        major_score = float(sum(1 for term in major_terms if term in text))
        if "remove" in text and not self._is_cleanup_remove_text(text):
            major_score += 1.0
        return ScoreMatrix(major=major_score, minor=minor_score, patch=patch_score)

    def _is_cleanup_remove_text(self, text: str) -> bool:
        """Return True when remove-related text clearly signals routine cleanup."""
        return any(
            re.search(pattern, text) is not None for pattern in _CLEANUP_REMOVE_PATTERNS
        )

    def _is_revert_commit(self, commit: Commit) -> bool:
        """Return True for revert commits that should stay maintenance-level."""
        subject = commit.subject.strip().casefold()
        body = commit.body.casefold()
        return subject.startswith("revert") or "this reverts commit" in body

    def _blast_radius_weight(self, changed_files: list[str]) -> float:
        """Compute path-role blast radius weight across changed files."""
        if not changed_files:
            return 1.0

        def _weight(path: str) -> float:
            normalized = path.strip().replace("\\", "/").casefold()
            if normalized.startswith("docs/"):
                return 0.1
            if normalized.startswith("tests/"):
                return 0.2
            if (
                normalized.startswith("core/")
                or normalized.startswith("api/")
                or normalized.startswith("cli/")
                or "/core/" in normalized
                or "/api/" in normalized
                or "/cli/" in normalized
            ):
                return 2.5
            if normalized.startswith("services/") or "/services/" in normalized:
                return 2.0
            if normalized.startswith("utils/") or "/utils/" in normalized:
                return 1.0
            return 1.0

        weights = [_weight(item) for item in changed_files]
        return max(weights)

    def _test_impact_ratio_multiplier(self, changed_files: list[str]) -> float:
        """Dampen score when test-only expansion dominates a commit's file set."""
        if not changed_files:
            return 1.0

        normalized_paths = [
            item.strip().replace("\\", "/").casefold() for item in changed_files
        ]
        test_files = [path for path in normalized_paths if path.startswith("tests/")]
        test_ratio = len(test_files) / max(len(normalized_paths), 1)
        non_test_count = len(normalized_paths) - len(test_files)

        if test_ratio >= 0.9:
            return 0.2
        if test_ratio >= 0.75 and non_test_count <= 1:
            return 0.35
        if test_ratio >= 0.6:
            return 0.55
        return 1.0

    def _deprecation_signal(self, commit: Commit) -> ScoreMatrix:
        """Boost MINOR signal when commit explicitly introduces deprecations."""
        if not commit.diff_text:
            return ScoreMatrix()

        matches = 0
        for line in commit.diff_text.splitlines():
            if not line.startswith("+"):
                continue
            if any(
                pattern.search(line) is not None for pattern in _DEPRECATION_PATTERNS
            ):
                matches += 1

        if matches == 0:
            return ScoreMatrix()
        return ScoreMatrix(minor=2.0 + (0.4 * max(matches - 1, 0)))

    def _schema_migration_signal(self, commit: Commit) -> ScoreMatrix:
        """Score migration and schema-change risk from touched files and diff text."""
        signal = ScoreMatrix()
        has_migrations = any(
            "migrations/" in item.strip().replace("\\", "/").casefold()
            or "alembic/" in item.strip().replace("\\", "/").casefold()
            or "versions/" in item.strip().replace("\\", "/").casefold()
            for item in commit.changed_files
        )
        if not has_migrations:
            return signal

        signal.minor += 2.0
        if commit.diff_text and any(
            re.search(pattern, commit.diff_text, re.IGNORECASE) is not None
            for pattern in _SCHEMA_DESTRUCTIVE_PATTERNS
        ):
            signal.major += 4.0
        return signal

    def _change_density_signal(self, commit: Commit) -> ScoreMatrix:
        """Score dense logical changes while damping formatter-like churn."""
        logical_changed, raw_changed = self._logical_diff_stats(commit.diff_text)
        total_lines = self._total_lines_for_paths(
            commit.changed_files, commit.full_hash
        )
        if total_lines <= 0:
            total_lines = max(raw_changed * 4, 1)

        density = logical_changed / max(total_lines, 1)
        signal = ScoreMatrix()

        if density >= 0.30:
            signal.minor += 1.6 + (density * 2.0)
            signal.major += 0.8 + density
        elif density >= 0.12:
            signal.minor += 0.8 + density
        elif logical_changed > 0:
            signal.patch += 0.4

        formatter_ratio = (logical_changed / raw_changed) if raw_changed > 0 else 1.0
        if raw_changed >= 40 and formatter_ratio <= 0.2:
            signal = signal.scaled(0.35)
        elif density <= 0.01 and raw_changed >= 20:
            signal = signal.scaled(0.5)
        return signal

    def _logical_diff_stats(self, diff_text: str) -> tuple[int, int]:
        """Return (logical_changed_lines, raw_changed_lines) from unified diff."""
        logical = 0
        raw = 0
        in_triple_docstring = False
        triple_delimiter = ""
        for line in diff_text.splitlines():
            if not line or line.startswith("+++") or line.startswith("---"):
                continue
            if not (line.startswith("+") or line.startswith("-")):
                continue
            raw += 1
            content = line[1:].strip()
            if not content:
                continue

            triple_match = re.match(
                r"^[rubf]*(\"\"\"|''')",
                content,
                re.IGNORECASE,
            )
            if triple_match:
                delimiter = triple_match.group(1)
                quote_count = content.count(delimiter)
                if in_triple_docstring and triple_delimiter == delimiter:
                    in_triple_docstring = False
                    triple_delimiter = ""
                elif quote_count == 1:
                    in_triple_docstring = True
                    triple_delimiter = delimiter
                continue

            if in_triple_docstring:
                if triple_delimiter and triple_delimiter in content:
                    in_triple_docstring = False
                    triple_delimiter = ""
                continue

            if re.match(r"^(#|//|/\*|\*|\*/)", content):
                continue
            logical += 1
        return logical, raw

    def _total_lines_for_paths(self, changed_files: list[str], commit_hash: str) -> int:
        """Estimate density denominator from file content at the target commit."""
        if self.project_root is None or self.git_exe is None or not commit_hash:
            return 0

        total = 0
        for relative in changed_files:
            source_at_commit = self._read_blob(commit_hash, relative)
            if source_at_commit is None:
                continue
            total += len(source_at_commit.splitlines())
        return total

    def _structural_signal(self, commit: Commit) -> ScoreMatrix:
        """Detect structural code changes via AST or regex fallback."""
        signal = ScoreMatrix()
        if not commit.diff_text:
            return signal

        added_defs: dict[str, str] = {}
        removed_defs: dict[str, str] = {}
        added_classes: set[str] = set()
        removed_classes: set[str] = set()

        for line in commit.diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                self._collect_signature_tokens(
                    line[1:],
                    target_defs=added_defs,
                    target_classes=added_classes,
                )
            elif line.startswith("-"):
                self._collect_signature_tokens(
                    line[1:],
                    target_defs=removed_defs,
                    target_classes=removed_classes,
                )

        added_only = set(added_defs) - set(removed_defs)
        removed_only = set(removed_defs) - set(added_defs)
        signal.minor += 0.9 * len(added_only)
        signal.major += 1.5 * len(removed_only)

        for name in set(added_defs).intersection(removed_defs):
            if added_defs[name] != removed_defs[name]:
                signal.major += 2.5

        signal.minor += 0.6 * len(added_classes - removed_classes)
        signal.major += 1.3 * len(removed_classes - added_classes)

        ast_signal = self._ast_structural_signal(commit)
        return signal.add(ast_signal)

    def _collect_signature_tokens(
        self,
        line: str,
        *,
        target_defs: dict[str, str],
        target_classes: set[str],
    ) -> None:
        """Collect function/class signature tokens from one source line."""
        fn_match = re.match(
            r"\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*:", line
        )
        if fn_match is not None:
            target_defs[fn_match.group(1)] = fn_match.group(2).strip()
            return
        cls_match = re.match(
            r"\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(.*\))?\s*:", line
        )
        if cls_match is not None:
            target_classes.add(cls_match.group(1))

    def _ast_structural_signal(self, commit: Commit) -> ScoreMatrix:
        """Compare AST symbol tables for changed Python files when possible."""
        if (
            self.project_root is None
            or self.git_exe is None
            or not commit.parent_hashes
            or not commit.changed_files
        ):
            return ScoreMatrix()

        parent = commit.parent_hashes[0]
        signal = ScoreMatrix()
        for file_path in commit.changed_files:
            if not file_path.endswith(".py"):
                continue
            old_src = self._read_blob(parent, file_path) or ""
            new_src = self._read_blob(commit.full_hash, file_path) or ""
            old_defs, old_classes = self._extract_symbols(old_src)
            new_defs, new_classes = self._extract_symbols(new_src)

            signal.minor += 0.5 * len(set(new_defs) - set(old_defs))
            signal.major += 1.0 * len(set(old_defs) - set(new_defs))

            for fn_name in set(old_defs).intersection(new_defs):
                if old_defs[fn_name] != new_defs[fn_name]:
                    signal.major += 2.0

            signal.minor += 0.4 * len(new_classes - old_classes)
            signal.major += 0.9 * len(old_classes - new_classes)

        return signal

    def _read_blob(self, revision: str, path: str) -> str | None:
        """Read file content from git object database for revision/path."""
        if self.project_root is None or self.git_exe is None:
            return None
        try:
            result = subprocess.run(
                [self.git_exe, "show", f"{revision}:{path}"],
                cwd=self.project_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )  # nosec B603
            if result.returncode != 0:
                return None
            return result.stdout
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return None

    def _extract_symbols(self, source: str) -> tuple[dict[str, str], set[str]]:
        """Extract function signatures and class names from full AST."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return self._extract_symbols_regex(source)

        defs: dict[str, str] = {}
        classes: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs[node.name] = self._serialize_args(node)
            elif isinstance(node, ast.ClassDef):
                classes.add(node.name)
        return defs, classes

    def _extract_symbols_regex(self, source: str) -> tuple[dict[str, str], set[str]]:
        """Fallback symbol extraction using regex when AST parsing fails."""
        defs: dict[str, str] = {}
        classes: set[str] = set()
        for line in source.splitlines():
            fn_match = re.match(
                r"\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*:",
                line,
            )
            if fn_match is not None:
                defs[fn_match.group(1)] = fn_match.group(2).strip()
                continue
            cls_match = re.match(
                r"\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(.*\))?\s*:",
                line,
            )
            if cls_match is not None:
                classes.add(cls_match.group(1))
        return defs, classes

    def _serialize_args(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Serialize function arguments for signature-change detection."""
        args = [arg.arg for arg in node.args.posonlyargs]
        args.extend(arg.arg for arg in node.args.args)
        if node.args.vararg is not None:
            args.append(f"*{node.args.vararg.arg}")
        args.extend(arg.arg for arg in node.args.kwonlyargs)
        if node.args.kwarg is not None:
            args.append(f"**{node.args.kwarg.arg}")
        return ",".join(args)

    def _dependency_signal(self, commit: Commit) -> ScoreMatrix:
        """Score dependency set shifts from requirements/pyproject changes."""
        dep_files = [
            path
            for path in commit.changed_files
            if path == "pyproject.toml" or path.startswith("requirements")
        ]
        if not dep_files:
            return ScoreMatrix()

        old_reqs, new_reqs = self._dependency_snapshots(commit, dep_files)
        if not old_reqs and not new_reqs:
            return ScoreMatrix()

        old_names = {self._normalize_dep_name(item) for item in old_reqs}
        new_names = {self._normalize_dep_name(item) for item in new_reqs}

        added = new_names - old_names
        removed = old_names - new_names

        signal = ScoreMatrix()
        signal.major += 1.4 * len(added)
        signal.major += 1.8 * len(removed)

        old_versions = self._parse_dep_versions(old_reqs)
        new_versions = self._parse_dep_versions(new_reqs)
        for name in set(old_versions).intersection(new_versions):
            old_major, old_minor = old_versions[name]
            new_major, new_minor = new_versions[name]
            if new_major != old_major:
                signal.major += 1.6
            elif new_minor != old_minor:
                signal.minor += 1.1

        return signal

    def _dependency_snapshots(
        self,
        commit: Commit,
        dep_files: list[str],
    ) -> tuple[set[str], set[str]]:
        """Return (old_deps, new_deps) for changed dependency descriptor files."""
        if (
            self.project_root is None
            or self.git_exe is None
            or not commit.parent_hashes
        ):
            return set(), set()

        old_deps: set[str] = set()
        new_deps: set[str] = set()
        parent = commit.parent_hashes[0]

        for rel in dep_files:
            old_blob = self._read_blob(parent, rel)
            new_blob = self._read_blob(commit.full_hash, rel)
            old_deps.update(self._extract_dependencies(rel, old_blob or ""))
            new_deps.update(self._extract_dependencies(rel, new_blob or ""))
        return old_deps, new_deps

    def _extract_dependencies(self, path: str, content: str) -> set[str]:
        """Extract dependency specifiers from known dependency files."""
        normalized = path.casefold()
        deps: set[str] = set()
        if normalized.endswith("pyproject.toml"):
            for quoted in re.findall(r"['\"]([^'\"]+)['\"]", content):
                if re.match(
                    r"^[A-Za-z0-9_.\-]+(?:\[[^\]]+\])?\s*(?:[<>=!~].+)?$", quoted
                ):
                    deps.add(quoted.strip())
            return deps

        for line in content.splitlines():
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            deps.add(cleaned)
        return deps

    def _normalize_dep_name(self, spec: str) -> str:
        """Normalize dependency specifier into comparable package name."""
        name = re.split(r"[<>=!~\[]", spec.strip(), maxsplit=1)[0]
        return name.strip().casefold()

    def _parse_dep_versions(self, specs: set[str]) -> dict[str, tuple[int, int]]:
        """Parse `major.minor` pairs from dependency spec strings."""
        parsed: dict[str, tuple[int, int]] = {}
        for spec in specs:
            name = self._normalize_dep_name(spec)
            version_match = re.search(r"(\d+)\.(\d+)", spec)
            if not name or version_match is None:
                continue
            parsed[name] = (int(version_match.group(1)), int(version_match.group(2)))
        return parsed

    def _only_docs_or_tests(self, changed_files: list[str]) -> bool:
        """Return True when commit touches only docs/ or tests/ trees."""
        if not changed_files:
            return False
        for item in changed_files:
            normalized = item.strip().replace("\\", "/").casefold()
            if normalized.startswith("docs/") or normalized.startswith("tests/"):
                continue
            return False
        return True

    def _decision_from_signal(
        self, signal: ScoreMatrix, *, current_version: str
    ) -> str:
        """Apply dynamic threshold gates to aggregated signal matrix."""
        confidence = signal.confidence()
        dominant = signal.dominant()

        bump = "patch"
        if dominant == "major" and signal.major >= 3.0 and confidence >= 0.5:
            bump = "major"
        elif dominant == "minor" and signal.minor >= 2.0 and confidence >= 0.5:
            bump = "minor"

        is_pre_1_0 = current_version.startswith("0.") or current_version == "0.0.0"
        if bump == "major" and is_pre_1_0:
            return "minor"
        return bump

    def _ai_assisted_evaluation(self, commits: list[Commit]) -> dict[str, float]:
        """Return AI fallback signal map when mathematical confidence is low."""
        if not commits:
            return {"major": 0.0, "minor": 0.0, "patch": 1.0}

        message_text = " ".join(commit.subject for commit in commits)
        if self.ai_fallback is not None:
            try:
                ai_result = (
                    self.ai_fallback(
                        f"Determine semver bump for these commits: {message_text}"
                    )
                    .strip()
                    .lower()
                )
                if "major" in ai_result:
                    return {"major": 1.0, "minor": 0.0, "patch": 0.0}
                if "minor" in ai_result:
                    return {"major": 0.0, "minor": 1.0, "patch": 0.0}
                return {"major": 0.0, "minor": 0.0, "patch": 1.0}
            except Exception as exc:
                log(
                    f"AI fallback failed: {exc}",
                    level="warning",
                    color="yellow",
                    component="release",
                )

        baseline = self._parse_heuristic(
            Commit(full_hash="", subject=message_text, body=""),
        )
        if baseline is None:
            return {"major": 0.2, "minor": 0.3, "patch": 0.5}
        if baseline.bump == "major":
            return {"major": 1.0, "minor": 0.2, "patch": 0.1}
        if baseline.bump == "minor":
            return {"major": 0.1, "minor": 1.0, "patch": 0.3}
        return {"major": 0.1, "minor": 0.3, "patch": 1.0}

    def _parse_heuristic(self, commit: Commit) -> CommitAnalysis | None:
        normalized = commit.subject.casefold()
        if self._is_revert_commit(commit):
            return CommitAnalysis(
                full_hash=commit.full_hash,
                original_subject=commit.subject,
                summary=commit.subject.strip(),
                commit_type="chore",
                bump="patch",
                source="heuristic",
            )
        if any(k in normalized for k in ("breaking", "drop")) or (
            "remove" in normalized and not self._is_cleanup_remove_text(normalized)
        ):
            return CommitAnalysis(
                full_hash=commit.full_hash,
                original_subject=commit.subject,
                summary=commit.subject.strip(),
                commit_type="feat",
                bump="major",
                source="heuristic",
            )
        if any(k in normalized for k in ("add", "new", "introduce", "implement")):
            return CommitAnalysis(
                full_hash=commit.full_hash,
                original_subject=commit.subject,
                summary=commit.subject.strip(),
                commit_type="feat",
                bump="minor",
                source="heuristic",
            )
        if any(k in normalized for k in ("fix", "bug", "error", "issue", "patch")):
            return CommitAnalysis(
                full_hash=commit.full_hash,
                original_subject=commit.subject,
                summary=commit.subject.strip(),
                commit_type="fix",
                bump="patch",
                source="heuristic",
            )
        if any(k in normalized for k in ("refactor", "cleanup", "docs", "chore")):
            return CommitAnalysis(
                full_hash=commit.full_hash,
                original_subject=commit.subject,
                summary=commit.subject.strip(),
                commit_type="chore",
                bump="patch",
                source="heuristic",
            )
        return None
