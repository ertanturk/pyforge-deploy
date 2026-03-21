"""Tests for release intelligence changelog engine."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyforge_deploy.builders.changelog_engine import ChangelogEngine, ReleasePlan
from pyforge_deploy.errors import ValidationError


def _cp(
    args: list[str], code: int, out: str = "", err: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, code, stdout=out, stderr=err)


def test_parse_commits_strict_and_misc_bucket() -> None:
    """Commit parser should classify strict, fuzzy, and truly unknown commits."""
    engine = ChangelogEngine(project_root=".")
    commits = [
        ("a" * 40, "feat(cli)!: add release intel", "BREAKING CHANGE: API changed"),
        ("b" * 40, "fix(parser): resolve edge case", ""),
        ("c" * 40, "implement plugin lifecycle", ""),
        ("d" * 40, "düzelttim parser regression", ""),
        ("e" * 40, "wip asdf", ""),
    ]

    parsed = engine.parse_commits(commits)

    assert parsed[0].commit_type == "feat"
    assert parsed[0].breaking is True
    assert parsed[1].scope == "parser"
    assert parsed[2].commit_type == "feat"
    assert parsed[3].commit_type == "fix"
    assert parsed[4].commit_type == "misc"


def test_generate_changelog_via_ai_no_key_returns_none() -> None:
    """AI tier should bypass quickly when no GEMINI_API_KEY is configured."""
    engine = ChangelogEngine(project_root=".")
    result = engine._generate_changelog_via_ai([("a" * 40, "feat: x", "")], "1.2.3")
    assert result is None


def test_generate_changelog_via_ai_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI tier should return markdown from selected provider response body."""
    engine = ChangelogEngine(project_root=".")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-openai-key")

    response_payload = (
        b'{"choices":[{"message":{"content":'
        b'"## [v1.2.3] - 2026-01-01\\n\\n### Features\\n* Add x (aaaaaaa)"}}]}'
    )
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = response_payload
    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.urllib_request.urlopen",
        lambda *_args, **_kwargs: mock_response,
    )

    markdown = engine._generate_changelog_via_ai(
        [("a" * 40, "feat: add x", "")],
        "1.2.3",
    )

    assert markdown is not None
    assert "### Features" in markdown


def test_generate_changelog_via_ai_invalid_key_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI tier should return None when no provider key is configured."""
    engine = ChangelogEngine(project_root=".")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("urlopen should not be called with no provider keys")

    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.urllib_request.urlopen",
        fail_if_called,
    )

    markdown = engine._generate_changelog_via_ai(
        [("a" * 40, "feat: add x", "")],
        "1.2.3",
    )

    assert markdown is None


def test_ai_provider_preference_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Router should prefer OpenAI, then Anthropic, then Gemini."""
    engine = ChangelogEngine(project_root=".")
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")

    provider = engine._select_ai_provider()

    assert provider is not None
    assert provider.name == "openai"


def test_openai_base_url_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Router should use OPENAI_BASE_URL for OpenAI-compatible endpoints."""
    engine = ChangelogEngine(project_root=".")
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    seen_urls: list[str] = []

    def fake_urlopen(request: object, **_kwargs: object) -> MagicMock:
        req = request
        if hasattr(req, "full_url"):
            seen_urls.append(req.full_url)
        mock_response = MagicMock()
        mock_response.__enter__.return_value.read.return_value = (
            b'{"choices":[{"message":{"content":"## [v1.2.3] - 2026-01-01"}}]}'
        )
        return mock_response

    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.urllib_request.urlopen",
        fake_urlopen,
    )

    markdown = engine._generate_changelog_via_ai(
        [("a" * 40, "messy commit", "")],
        "1.2.3",
    )

    assert markdown is not None
    assert seen_urls
    assert seen_urls[0] == "http://localhost:11434/v1/chat/completions"


def test_plan_release_uses_ai_and_skips_local_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan_release should pre-filter strict commits and AI-process malformed ones."""
    engine = ChangelogEngine(project_root=".")

    def fake_run_git(
        args: list[str], *, check: bool = False
    ) -> subprocess.CompletedProcess[str] | None:
        del check
        if args[:2] == ["rev-parse", "--is-inside-work-tree"]:
            return _cp(args, 0, "true\n")
        if args[:3] == ["describe", "--tags", "--abbrev=0"]:
            return _cp(args, 0, "v1.2.3\n")
        if args[0] == "log" and "--format=%H|%s|%b" in args:
            return _cp(
                args,
                0,
                f"{'a' * 40}|feat(api): add endpoint|\n"
                f"{'b' * 40}|totally random commit text|\n",
            )
        return _cp(args, 1, "", "unexpected")

    monkeypatch.setattr(engine, "_run_git", fake_run_git)
    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.get_dynamic_version",
        lambda **_kw: "1.2.3",
    )
    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.suggest_bump_from_git",
        lambda: "default",
    )
    sent_to_ai: list[tuple[str, str, str]] = []

    def fake_ai(
        raw: list[tuple[str, str, str]],
        _version: str,
    ) -> str | None:
        sent_to_ai.extend(raw)
        return "## [v1.3.0] - 2026-03-21\n\n### Maintenance\n* normalized"

    monkeypatch.setattr(engine, "_generate_changelog_via_ai", fake_ai)

    plan = engine.plan_release()

    assert plan is not None
    assert plan.next_version == "1.3.0"
    assert len(plan.commits) == 2
    assert sent_to_ai == [("b" * 40, "totally random commit text", "")]
    assert "### Features" in plan.markdown_block
    assert "### Maintenance" in plan.markdown_block


def test_generate_changelog_via_ai_chunking_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI tier should chunk oversized commit lists and merge responses."""
    engine = ChangelogEngine(project_root=".")
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")

    call_count = {"n": 0}

    def fake_send(_provider: object, _prompt: str) -> str:
        call_count["n"] += 1
        return "### Maintenance\n* chunk item"

    monkeypatch.setattr(engine, "_send_ai_request", fake_send)
    commits = [(str(i).zfill(40), f"messy {i}", "") for i in range(450)]

    markdown = engine._generate_changelog_via_ai(commits, "1.2.3")

    assert markdown is not None
    assert markdown.startswith("## [v1.2.3]")
    assert call_count["n"] == 3


def test_decide_bump_pride_modes() -> None:
    """Auto-bump should use Pride modes (proud/default/shame)."""
    engine = ChangelogEngine(project_root=".")

    major_commit = engine.parse_commits([("a" * 40, "feat!: break everything", "")])
    assert engine.decide_bump(major_commit) == "proud"

    minor_commit = engine.parse_commits([("b" * 40, "feat(core): add endpoint", "")])
    assert engine.decide_bump(minor_commit) == "default"

    patch_commit = engine.parse_commits([("c" * 40, "fix(core): patch bug", "")])
    assert engine.decide_bump(patch_commit) == "shame"


def test_plan_release_from_tag_and_git_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan should derive next version and markdown from git metadata."""
    engine = ChangelogEngine(project_root=".")

    def fake_run_git(
        args: list[str], *, check: bool = False
    ) -> subprocess.CompletedProcess[str] | None:
        del check
        if args[:2] == ["rev-parse", "--is-inside-work-tree"]:
            return _cp(args, 0, "true\n")
        if args[:3] == ["describe", "--tags", "--abbrev=0"]:
            return _cp(args, 0, "v1.2.3\n")
        if args[0] == "log" and "--format=%H|%s|%b" in args:
            return _cp(
                args,
                0,
                "{}|feat(api): add endpoint|\n{}|fix: correct typo|\n".format(
                    "a" * 40, "b" * 40
                ),
            )
        return _cp(args, 1, "", "unexpected")

    monkeypatch.setattr(engine, "_run_git", fake_run_git)
    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.get_dynamic_version",
        lambda **_kw: "1.2.3",
    )
    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.suggest_bump_from_git",
        lambda: "default",
    )
    plan = engine.plan_release()

    assert plan is not None
    assert plan.next_version == "1.3.0"
    assert "## [v1.3.0]" in plan.markdown_block
    assert "### Features" in plan.markdown_block
    assert "### Bug Fixes" in plan.markdown_block


def test_execute_dry_run_prints_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Dry-run should print markdown and avoid writes or git release ops."""
    engine = ChangelogEngine(project_root=tmp_path)

    def fake_run_git(
        args: list[str], *, check: bool = False
    ) -> subprocess.CompletedProcess[str] | None:
        del check
        if args[:2] == ["rev-parse", "--is-inside-work-tree"]:
            return _cp(args, 0, "true\n")
        if args[:3] == ["describe", "--tags", "--abbrev=0"]:
            return _cp(args, 0, "v0.1.0\n")
        if args[0] == "log" and "--format=%H|%s|%b" in args:
            return _cp(args, 0, f"{'a' * 40}|fix: bug fix|\n")
        return _cp(args, 0, "")

    monkeypatch.setattr(engine, "_run_git", fake_run_git)
    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.get_dynamic_version",
        lambda **_kw: "0.1.0",
    )
    monkeypatch.setattr(
        "pyforge_deploy.builders.changelog_engine.suggest_bump_from_git",
        lambda: "shame",
    )

    plan = engine.execute(dry_run=True)

    assert plan is not None
    assert not (tmp_path / "CHANGELOG.md").exists()
    captured = capsys.readouterr().out
    assert "## [v0.1.1]" in captured


def test_dirty_tree_validation_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dirty tree with unrelated files should raise ValidationError."""
    engine = ChangelogEngine(project_root=".")

    def fake_run_git(
        args: list[str], *, check: bool = False
    ) -> subprocess.CompletedProcess[str] | None:
        del check
        if args[:2] == ["status", "--porcelain"]:
            return _cp(args, 0, " M README.md\n")
        return _cp(args, 0, "")

    monkeypatch.setattr(engine, "_run_git", fake_run_git)

    with pytest.raises(ValidationError):
        engine._assert_clean_tree()


def test_execute_allow_dirty_bypasses_clean_tree_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """allow_dirty should bypass clean-tree guard and continue release flow."""
    engine = ChangelogEngine(project_root=tmp_path)

    monkeypatch.setattr(
        engine,
        "plan_release",
        lambda target_version=None: ReleasePlan(
            base_ref="v1.0.0",
            commits=[],
            next_version="1.0.1",
            markdown_block="## [v1.0.1] - 2026-03-21\n",
        ),
    )

    def raise_if_called() -> None:
        raise ValidationError("clean tree check should be bypassed")

    monkeypatch.setattr(engine, "_assert_clean_tree", raise_if_called)
    monkeypatch.setattr(engine, "_run_release_git_ops", lambda _version: None)

    plan = engine.execute(allow_dirty=True)

    assert plan is not None
    assert (tmp_path / "CHANGELOG.md").exists()


def test_resolve_next_version_strips_v_prefix() -> None:
    """Explicit target versions with 'v' should normalize to plain x.y.z."""
    engine = ChangelogEngine(project_root=".")
    assert engine._resolve_next_version("shame", "v1.2.3") == "1.2.3"


def test_run_release_git_ops_uses_single_v_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Release git operations should never produce a double-v tag."""
    engine = ChangelogEngine(project_root=".")
    commands: list[list[str]] = []

    def fake_run_git(
        args: list[str], *, check: bool = False
    ) -> subprocess.CompletedProcess[str] | None:
        del check
        commands.append(args)
        return _cp(args, 0, "", "")

    monkeypatch.setattr(engine, "_run_git", fake_run_git)

    engine._run_release_git_ops("v1.2.3")

    assert ["commit", "-m", "chore(release): v1.2.3 [skip ci]"] in commands
    assert ["tag", "v1.2.3"] in commands
    assert ["tag", "vv1.2.3"] not in commands
