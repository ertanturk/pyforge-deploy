"""Tests for focused release orchestration components."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pyforge_deploy.release.changelog_builder import ChangelogBuilder
from pyforge_deploy.release.commit_analyzer import Commit, CommitAnalyzer, ScoreMatrix
from pyforge_deploy.release.publisher import Publisher
from pyforge_deploy.release.service import ReleasePlan, ReleaseService
from pyforge_deploy.release.version_resolver import VersionResolver


def test_commit_analyzer_conventional_heuristic_and_ai_fallback() -> None:
    """Analyzer should prioritize conventional, then heuristic, then AI fallback."""
    ai_used: dict[str, bool] = {"called": False}

    def fake_ai(subject: str) -> str:
        ai_used["called"] = True
        return f"normalized: {subject}"

    analyzer = CommitAnalyzer(ai_fallback=fake_ai)
    commits = [
        Commit(full_hash="a" * 40, subject="feat: add auth", body=""),
        Commit(full_hash="b" * 40, subject="fix login bug quickly", body=""),
        Commit(full_hash="c" * 40, subject="???", body=""),
    ]

    result = analyzer.analyze(commits)

    assert result[0].bump == "minor"
    assert result[0].source == "conventional"
    assert result[1].bump == "patch"
    assert result[1].source == "heuristic"
    assert result[2].source == "ai"
    assert ai_used["called"] is True


def test_commit_analyzer_works_without_ai_keys() -> None:
    """Analyzer should still classify malformed commits without AI fallback."""
    analyzer = CommitAnalyzer(ai_fallback=None)
    result = analyzer.analyze([Commit(full_hash="d" * 40, subject="???", body="")])

    assert result[0].bump == "patch"
    assert result[0].source in {"heuristic", "ai"}


def test_ai_assisted_evaluation_uses_ai_fallback_callable() -> None:
    """Low-confidence AI path should call configured AI fallback callable."""
    seen: dict[str, str] = {}

    def fake_ai(prompt: str) -> str:
        seen["prompt"] = prompt
        return "MINOR"

    analyzer = CommitAnalyzer(ai_fallback=fake_ai)

    result = analyzer._ai_assisted_evaluation(
        [Commit(full_hash="a" * 40, subject="refactor parser", body="")]
    )

    assert "Determine semver bump for these commits:" in seen["prompt"]
    assert result == {"major": 0.0, "minor": 1.0, "patch": 0.0}


def test_ai_assisted_evaluation_falls_back_when_ai_errors() -> None:
    """AI fallback failures should gracefully return heuristic-based scoring."""

    def failing_ai(_prompt: str) -> str:
        raise RuntimeError("ai unavailable")

    analyzer = CommitAnalyzer(ai_fallback=failing_ai)
    result = analyzer._ai_assisted_evaluation(
        [Commit(full_hash="a" * 40, subject="add endpoint", body="")]
    )

    assert result["minor"] > result["patch"]


def test_version_resolver_handles_initial_release() -> None:
    """Resolver should suggest a sane initial version when tags are absent."""
    resolver = VersionResolver()

    suggestion = resolver.suggest_next_version(latest_tag=None, bumps=["patch"])

    assert suggestion.current_version == "0.0.0"
    assert suggestion.suggested_version == "0.1.0"


def test_version_resolver_initial_release_respects_major_bump() -> None:
    """Initial releases should promote to 1.0.0 when bump decision is major."""
    resolver = VersionResolver()

    suggestion = resolver.suggest_next_version(latest_tag=None, bumps=["major"])

    assert suggestion.current_version == "0.0.0"
    assert suggestion.suggested_version == "1.0.0"
    assert suggestion.bump == "major"


def test_release_service_plan_handles_no_tags_and_messy_commits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Release service should plan initial release from messy commit history."""

    monkeypatch.setattr(
        "pyforge_deploy.release.service.shutil.which", lambda _name: "git"
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        if cmd[:3] == ["git", "describe", "--tags"]:
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="no tags")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=("a" * 40 + "\x1f" + "bad commit text" + "\x1f" + "" + "\x1e"),
            stderr="",
        )

    monkeypatch.setattr("pyforge_deploy.release.service.subprocess.run", fake_run)

    service = ReleaseService(project_root=tmp_path)
    plan = service.plan()

    assert plan.latest_tag is None
    assert plan.suggested_version == "0.1.0"
    assert "## v0.1.0" in plan.changelog_markdown


def test_release_service_plan_passes_current_version_to_analyzer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Release planning should pass resolver-derived current version to analyzer."""

    monkeypatch.setattr(
        "pyforge_deploy.release.service.shutil.which", lambda _name: "git"
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        if cmd[:3] == ["git", "describe", "--tags"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="v0.2.0\n", stderr="")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=("a" * 40 + "\x1f" + "feat: x" + "\x1f" + "" + "\x1e"),
            stderr="",
        )

    monkeypatch.setattr("pyforge_deploy.release.service.subprocess.run", fake_run)

    service = ReleaseService(project_root=tmp_path)
    seen: dict[str, str] = {}

    def fake_determine_bump(
        _commits: list[Commit], *, current_version: str = "0.0.0"
    ) -> str:
        seen["current_version"] = current_version
        return "patch"

    monkeypatch.setattr(service.analyzer, "determine_bump", fake_determine_bump)

    _ = service.plan()

    assert seen["current_version"] == "0.2.0"


def test_release_service_apply_dry_run_is_realistic(tmp_path: Path) -> None:
    """Dry-run apply should delegate with no file mutation side effects."""
    service = ReleaseService(project_root=tmp_path)
    seen: dict[str, object] = {}

    def fake_publish(**kwargs: object) -> None:
        seen.update(kwargs)

    service.publisher.publish = fake_publish  # type: ignore[method-assign]

    plan = ReleasePlan(
        latest_tag="v1.2.3",
        commits=[],
        suggested_version="1.2.4",
        changelog_markdown="## v1.2.4\n- Changed\n  - maintenance",
    )
    service.apply(plan, local_publish=False, dry_run=True)

    assert seen["version"] == "1.2.4"
    assert seen["dry_run"] is True


def test_changelog_builder_outputs_structured_sections() -> None:
    """Changelog output should remain clean and grouped."""
    commits = CommitAnalyzer().analyze(
        [
            Commit(full_hash="a" * 40, subject="feat: add auth", body=""),
            Commit(full_hash="b" * 40, subject="fix: login bug", body=""),
        ]
    )
    markdown = ChangelogBuilder().build("1.3.0", commits)

    assert "## v1.3.0" in markdown
    assert "- Added" in markdown
    assert "- Fixed" in markdown


def test_changelog_builder_deduplicates_duplicate_summaries() -> None:
    """Repeated summaries should appear once per changelog category."""
    commits = [
        Commit(
            full_hash="a" * 40,
            subject="feat: add auth",
            body="",
        ),
        Commit(
            full_hash="b" * 40,
            subject="feat: add auth",
            body="",
        ),
        Commit(
            full_hash="c" * 40,
            subject="fix: typo",
            body="",
        ),
        Commit(
            full_hash="d" * 40,
            subject="fix: typo",
            body="",
        ),
    ]
    analyses = CommitAnalyzer().analyze(commits)

    markdown = ChangelogBuilder().build("1.3.1", analyses)

    assert markdown.count("  - add auth") == 1
    assert markdown.count("  - typo") == 1


def test_determine_bump_returns_patch_on_zero_commits() -> None:
    """Heuristic engine should safely default to patch with empty history."""
    analyzer = CommitAnalyzer()
    assert analyzer.determine_bump([]) == "patch"


def test_determine_bump_applies_major_threshold_gate(monkeypatch) -> None:
    """Major decision should require both score and confidence threshold."""
    analyzer = CommitAnalyzer()
    monkeypatch.setattr(
        analyzer,
        "_aggregate_signal",
        lambda _commits: ScoreMatrix(major=8.0, minor=2.0, patch=1.0),
    )

    decision = analyzer.determine_bump(
        [Commit(full_hash="a" * 40, subject="x", body="")],
        current_version="1.2.3",
    )
    assert decision == "major"


def test_decision_from_signal_dynamic_major_threshold() -> None:
    """Major-dominant signals above baseline should classify as major."""
    analyzer = CommitAnalyzer()

    decision = analyzer._decision_from_signal(
        ScoreMatrix(major=4.0, minor=1.0, patch=0.5),
        current_version="1.2.3",
    )

    assert decision == "major"


def test_determine_bump_scales_major_to_minor_for_pre_1_0(monkeypatch) -> None:
    """Pre-1.0 projects should downgrade major breaks to minor bumps."""
    analyzer = CommitAnalyzer()
    monkeypatch.setattr(
        analyzer,
        "_aggregate_signal",
        lambda _commits: ScoreMatrix(major=8.0, minor=0.5, patch=0.2),
    )

    decision = analyzer.determine_bump(
        [Commit(full_hash="a" * 40, subject="x", body="")],
        current_version="0.3.1",
    )

    assert decision == "minor"


def test_determine_bump_low_confidence_uses_ai_and_allows_override(
    monkeypatch,
) -> None:
    """Low confidence path should consult AI signal and honor manual override."""
    analyzer = CommitAnalyzer()
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        analyzer,
        "_aggregate_signal",
        lambda _commits: ScoreMatrix(major=1.0, minor=1.0, patch=1.0),
    )
    monkeypatch.setattr(
        analyzer,
        "_ai_assisted_evaluation",
        lambda _commits: {"major": 0.1, "minor": 0.9, "patch": 0.2},
    )
    monkeypatch.setattr(sys, "stdin", type("_TTY", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda _prompt: "major")

    decision = analyzer.determine_bump(
        [Commit(full_hash="b" * 40, subject="x", body="")]
    )
    assert decision == "major"


def test_determine_bump_skips_prompt_when_ci_pseudo_tty(monkeypatch) -> None:
    """CI env should bypass interactive prompt even when stdin reports TTY."""
    analyzer = CommitAnalyzer()
    monkeypatch.setattr(
        analyzer,
        "_aggregate_signal",
        lambda _commits: ScoreMatrix(major=1.0, minor=1.0, patch=1.0),
    )
    monkeypatch.setattr(
        analyzer,
        "_ai_assisted_evaluation",
        lambda _commits: {"major": 0.1, "minor": 0.9, "patch": 0.2},
    )
    monkeypatch.setattr(sys, "stdin", type("_TTY", (), {"isatty": lambda self: True})())
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(
            AssertionError("input must not be called in CI")
        ),
    )

    decision = analyzer.determine_bump(
        [Commit(full_hash="b" * 40, subject="x", body="")]
    )

    assert decision == "minor"


def test_logical_diff_stats_ignores_python_docstring_blocks() -> None:
    """Triple-quoted docstring-only diff blocks should not count as code changes."""
    analyzer = CommitAnalyzer()
    logical, raw = analyzer._logical_diff_stats(
        "\n".join(
            [
                '+"""Large docs block',
                "+line one",
                "+line two",
                '+line three"""',
                "+value = 1",
            ]
        )
    )

    assert raw == 5
    assert logical == 1


def test_filter_noise_skips_release_chore_commits() -> None:
    """Automated release chores should be filtered from scoring input."""
    analyzer = CommitAnalyzer()
    commits = [
        Commit(full_hash="a" * 40, subject="chore(release): v1.2.3", body=""),
        Commit(full_hash="b" * 40, subject="feat: add api", body=""),
    ]

    filtered = analyzer._filter_noise(commits)

    assert len(filtered) == 1
    assert filtered[0].subject == "feat: add api"


def test_change_density_passes_commit_hash_to_line_counter(monkeypatch) -> None:
    """Density denominator should be read from the analyzed commit revision."""
    analyzer = CommitAnalyzer()
    commit = Commit(
        full_hash="c" * 40,
        subject="feat: add parser",
        body="",
        changed_files=["src/example.py"],
        diff_text="+x = 1\n+y = 2",
    )
    seen: dict[str, str] = {}

    def fake_total_lines(changed_files: list[str], commit_hash: str) -> int:
        del changed_files
        seen["hash"] = commit_hash
        return 50

    monkeypatch.setattr(analyzer, "_total_lines_for_paths", fake_total_lines)

    _ = analyzer._change_density_signal(commit)

    assert seen["hash"] == "c" * 40


def test_blast_radius_uses_max_not_average() -> None:
    """Core file changes must not be diluted by many docs file changes."""
    analyzer = CommitAnalyzer()

    weight = analyzer._blast_radius_weight(
        ["core/api.py"] + [f"docs/note-{index}.md" for index in range(9)]
    )

    assert weight == 2.5


def test_cleanup_remove_text_does_not_score_major() -> None:
    """Routine remove-cleanup messages should not trigger major scoring."""
    analyzer = CommitAnalyzer()
    commit = Commit(
        full_hash="a" * 40,
        subject="remove unused imports",
        body="",
    )

    signal = analyzer._message_signal(commit)

    assert signal.major == 0.0


def test_message_signal_security_override_forces_patch_dominance() -> None:
    """Security-related commit text should force high-confidence patch scoring."""
    analyzer = CommitAnalyzer()
    commit = Commit(
        full_hash="a" * 40,
        subject="hotfix: mitigate CVE-2026-12345",
        body="security vulnerability in dependency",
    )

    signal = analyzer._message_signal(commit)

    assert signal.patch == 100.0
    assert signal.major == 0.0
    assert signal.minor == 0.0


def test_revert_commit_short_circuits_to_patch_signal() -> None:
    """Revert commits should be treated as maintenance-level patch changes."""
    analyzer = CommitAnalyzer()
    commit = Commit(
        full_hash="f" * 40,
        subject='revert "feat: add endpoint"',
        body="This reverts commit 123456.",
        changed_files=["core/api.py"],
        diff_text="-def foo():\n+pass",
    )

    signal = analyzer._score_commit(commit, time_delta_seconds=1200)

    assert signal.patch == 1.0
    assert signal.major == 0.0
    assert signal.minor == 0.0


def test_test_impact_ratio_multiplier_dampens_test_heavy_commits() -> None:
    """Test-heavy commits should be dampened despite critical-file blast radius."""
    analyzer = CommitAnalyzer()
    commit = Commit(
        full_hash="a" * 40,
        subject="feat: harden api behavior",
        body="",
        changed_files=["core/api.py"] + [f"tests/test_api_{i}.py" for i in range(20)],
        diff_text="+def new_api():\n+    return 1",
    )

    signal = analyzer._score_commit(commit, time_delta_seconds=0)

    assert signal.minor < 2.5


def test_deprecation_signal_boosts_minor_score() -> None:
    """Deprecation markers in diff should immediately increase minor signal."""
    analyzer = CommitAnalyzer()
    commit = Commit(
        full_hash="a" * 40,
        subject="docs: annotate deprecation",
        body="",
        diff_text=(
            "+@deprecated\n"
            "+def old_api():\n"
            "+    warnings.warn('old_api is deprecated', DeprecationWarning)\n"
        ),
    )

    signal = analyzer._deprecation_signal(commit)

    assert signal.minor >= 2.0
    assert signal.major == 0.0


def test_extract_symbols_includes_class_methods() -> None:
    """AST symbol extraction should include methods nested in classes."""
    analyzer = CommitAnalyzer()

    defs, classes = analyzer._extract_symbols(
        "\n".join(
            [
                "class Client:",
                "    def connect(self, url):",
                "        return url",
            ]
        )
    )

    assert "Client" in classes
    assert "connect" in defs


def test_schema_migration_signal_detects_destructive_changes() -> None:
    """Destructive migration diffs should elevate major migration risk."""
    analyzer = CommitAnalyzer()
    commit = Commit(
        full_hash="a" * 40,
        subject="feat: migrate schema",
        body="",
        changed_files=["alembic/versions/20260322_drop_column.py"],
        diff_text="+op.drop_column('users', 'legacy_id')\n",
    )

    signal = analyzer._schema_migration_signal(commit)

    assert signal.minor >= 2.0
    assert signal.major >= 4.0


def test_version_resolver_parses_prerelease_tag_without_reset() -> None:
    """Pre-release tags should preserve base version when calculating next bump."""
    resolver = VersionResolver()

    suggestion = resolver.suggest_next_version(latest_tag="v1.2.3-rc1", bumps=["patch"])

    assert suggestion.current_version == "1.2.3-rc1"
    assert suggestion.suggested_version == "1.2.4"


def test_changelog_builder_update_file_is_idempotent(tmp_path: Path) -> None:
    """Existing version sections should not be duplicated on repeated writes."""
    changelog = tmp_path / "CHANGELOG.md"
    section = "## v1.2.0\n- Added\n  - feature"
    changelog.write_text(
        (
            "# Changelog\n\n"
            "## v1.2.0\n"
            "- Added\n"
            "  - feature\n\n"
            "## v1.1.9\n"
            "- Fixed\n"
            "  - bug\n"
        ),
        encoding="utf-8",
    )

    ChangelogBuilder().update_file(changelog, section)

    content = changelog.read_text(encoding="utf-8")
    assert content.count("## v1.2.0") == 1


def test_changelog_builder_update_file_detects_version_with_date_header(
    tmp_path: Path,
) -> None:
    """Version-aware duplicate detection should handle dated changelog headers."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## v1.2.0 - March 22\n- Added\n  - feature\n",
        encoding="utf-8",
    )

    ChangelogBuilder().update_file(
        changelog,
        "## v1.2.0\n- Fixed\n  - bug",
    )

    content = changelog.read_text(encoding="utf-8")
    assert content.count("1.2.0") == 1


def test_publisher_skips_commit_when_no_staged_changes(
    monkeypatch, tmp_path: Path
) -> None:
    """Publisher should not crash when git has nothing staged to commit."""
    commands: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = args[0]
        assert isinstance(command, list)
        commands.append(command)
        if command[:4] == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["git", "rev-parse", "--verify"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        if command[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:4] == ["git", "config", "--get", "branch.main.remote"]:
            return subprocess.CompletedProcess(command, 0, stdout="origin\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("pyforge_deploy.release.publisher.subprocess.run", fake_run)

    publisher = Publisher(project_root=tmp_path)
    publisher.git_exe = "git"

    publisher._git_commit_and_tag("1.2.3")

    assert any(command[:2] == ["git", "add"] for command in commands)
    assert any(
        command[:4] == ["git", "diff", "--cached", "--quiet"] for command in commands
    )
    assert not any(command[:2] == ["git", "commit"] for command in commands)


def test_publisher_tag_exists_returns_before_staging(
    monkeypatch, tmp_path: Path
) -> None:
    """If release tag already exists, publisher should not stage/commit again."""
    commands: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = args[0]
        assert isinstance(command, list)
        commands.append(command)
        if command[:4] == ["git", "rev-parse", "--verify", "refs/tags/v1.2.3"]:
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
        if command[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:4] == ["git", "config", "--get", "branch.main.remote"]:
            return subprocess.CompletedProcess(command, 0, stdout="origin\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("pyforge_deploy.release.publisher.subprocess.run", fake_run)

    publisher = Publisher(project_root=tmp_path)
    publisher.git_exe = "git"

    publisher._git_commit_and_tag("1.2.3")

    assert not any(command[:2] == ["git", "add"] for command in commands)
    assert not any(command[:2] == ["git", "commit"] for command in commands)
    assert ["git", "push", "origin", "main"] in commands
    assert ["git", "push", "origin", "v1.2.3"] in commands


def test_publisher_pushes_branch_and_tag_after_tagging(
    monkeypatch, tmp_path: Path
) -> None:
    """Publisher should push both branch and release tag to remote."""
    commands: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = args[0]
        assert isinstance(command, list)
        commands.append(command)
        if command[:4] == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        if command[:4] == ["git", "rev-parse", "--verify", "refs/tags/v1.2.3"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        if command[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[:4] == ["git", "config", "--get", "branch.main.remote"]:
            return subprocess.CompletedProcess(command, 0, stdout="origin\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("pyforge_deploy.release.publisher.subprocess.run", fake_run)

    publisher = Publisher(project_root=tmp_path)
    publisher.git_exe = "git"

    publisher._git_commit_and_tag("1.2.3")

    assert ["git", "push", "origin", "main"] in commands
    assert ["git", "push", "origin", "v1.2.3"] in commands


def test_publish_local_publish_pushes_after_distributor_deploy(
    monkeypatch, tmp_path: Path
) -> None:
    """Local publish should push refs only after distributor deploy succeeds."""
    events: list[str] = []

    publisher = Publisher(project_root=tmp_path)

    monkeypatch.setattr(
        publisher, "_write_version", lambda _version: events.append("write")
    )
    monkeypatch.setattr(
        "pyforge_deploy.release.publisher.ChangelogBuilder.update_file",
        lambda _self, _path, _section: events.append("changelog"),
    )
    monkeypatch.setattr(
        publisher,
        "_git_commit_and_tag_local_only",
        lambda _version: events.append("local_tag"),
    )
    monkeypatch.setattr(
        publisher,
        "_push_release_refs",
        lambda _tag: events.append("push"),
    )

    class _FakeDistributor:
        def __init__(self, **_kwargs: object) -> None:
            self.auto_confirm = False

        def deploy(self) -> None:
            events.append("deploy")

    monkeypatch.setattr(
        "pyforge_deploy.release.publisher.PyPIDistributor", _FakeDistributor
    )

    publisher.publish(
        version="1.2.3",
        changelog_markdown="## v1.2.3\n- Added\n  - x",
        local_publish=True,
        dry_run=False,
    )

    assert events == ["write", "changelog", "local_tag", "deploy", "push"]


def test_publish_local_publish_failure_does_not_push(
    monkeypatch, tmp_path: Path
) -> None:
    """Failed local publish should not push refs to remote."""
    events: list[str] = []

    publisher = Publisher(project_root=tmp_path)

    monkeypatch.setattr(
        publisher, "_write_version", lambda _version: events.append("write")
    )
    monkeypatch.setattr(
        "pyforge_deploy.release.publisher.ChangelogBuilder.update_file",
        lambda _self, _path, _section: events.append("changelog"),
    )
    monkeypatch.setattr(
        publisher,
        "_git_commit_and_tag_local_only",
        lambda _version: events.append("local_tag"),
    )
    monkeypatch.setattr(
        publisher,
        "_push_release_refs",
        lambda _tag: events.append("push"),
    )

    class _FailingDistributor:
        def __init__(self, **_kwargs: object) -> None:
            self.auto_confirm = False

        def deploy(self) -> None:
            events.append("deploy")
            raise RuntimeError("publish failed")

    monkeypatch.setattr(
        "pyforge_deploy.release.publisher.PyPIDistributor",
        _FailingDistributor,
    )

    try:
        publisher.publish(
            version="1.2.3",
            changelog_markdown="## v1.2.3\n- Added\n  - x",
            local_publish=True,
            dry_run=False,
        )
    except RuntimeError:
        pass

    assert "push" not in events


def test_collect_commits_since_caps_first_release_history(
    monkeypatch, tmp_path: Path
) -> None:
    """First release should limit git log history to a safe max commit count."""
    seen: dict[str, list[str]] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = args[0]
        assert isinstance(command, list)
        if command[:2] == ["git", "log"]:
            seen["command"] = command
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("pyforge_deploy.release.service.shutil.which", lambda _n: "git")
    monkeypatch.setattr("pyforge_deploy.release.service.subprocess.run", fake_run)

    service = ReleaseService(project_root=tmp_path)
    service._collect_commits_since(None)

    command = seen["command"]
    assert "-n" in command
    assert "50" in command


def test_collect_diff_for_commit_does_not_exclude_added_files(
    monkeypatch, tmp_path: Path
) -> None:
    """Diff collection should avoid lowercase diff filters that exclude additions."""
    seen: dict[str, list[str]] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = args[0]
        assert isinstance(command, list)
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="+new line\n", stderr="")

    monkeypatch.setattr("pyforge_deploy.release.service.shutil.which", lambda _n: "git")
    monkeypatch.setattr("pyforge_deploy.release.service.subprocess.run", fake_run)

    service = ReleaseService(project_root=tmp_path)
    _ = service._collect_diff_for_commit("abc123")

    command = seen["command"]
    assert "--text" in command
    assert "--diff-filter=a" not in command


def test_collect_changed_files_timeout_returns_empty(
    monkeypatch, tmp_path: Path
) -> None:
    """Changed file collection should fail closed on subprocess timeout."""

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=15)

    monkeypatch.setattr("pyforge_deploy.release.service.shutil.which", lambda _n: "git")
    monkeypatch.setattr("pyforge_deploy.release.service.subprocess.run", fake_run)

    service = ReleaseService(project_root=tmp_path)

    assert service._collect_changed_files_for_commit("abc123") == []


def test_collect_diff_timeout_returns_empty(monkeypatch, tmp_path: Path) -> None:
    """Diff collection should fail closed on subprocess timeout."""

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=20)

    monkeypatch.setattr("pyforge_deploy.release.service.shutil.which", lambda _n: "git")
    monkeypatch.setattr("pyforge_deploy.release.service.subprocess.run", fake_run)

    service = ReleaseService(project_root=tmp_path)

    assert service._collect_diff_for_commit("abc123") == ""


def test_read_blob_timeout_returns_none(monkeypatch) -> None:
    """Blob reads should return None on subprocess timeout instead of crashing."""
    analyzer = CommitAnalyzer(project_root=".")
    analyzer.git_exe = "git"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git", "show"], timeout=10)

    monkeypatch.setattr(
        "pyforge_deploy.release.commit_analyzer.subprocess.run", fake_run
    )

    result = analyzer._read_blob("abc123", "src/example.py")

    assert result is None


def test_ast_structural_signal_handles_added_file_without_parent_blob(
    monkeypatch,
) -> None:
    """AST structural scoring should include newly added Python files."""
    analyzer = CommitAnalyzer(project_root=".")
    commit = Commit(
        full_hash="n" * 40,
        subject="feat: add module",
        body="",
        parent_hashes=["p" * 40],
        changed_files=["src/new_module.py"],
    )

    def fake_read_blob(revision: str, path: str) -> str | None:
        if revision == "p" * 40:
            return None
        return "def new_feature(x, y):\n    return x + y\n\nclass API:\n    pass\n"

    monkeypatch.setattr(analyzer, "_read_blob", fake_read_blob)

    signal = analyzer._ast_structural_signal(commit)

    assert signal.minor > 0.0
