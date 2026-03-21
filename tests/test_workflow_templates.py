"""Tests for workflow templates and CI action metadata."""

from pathlib import Path

from pyforge_deploy.templates.workflows import GITHUB_RELEASE_YAML


def test_release_workflow_template_uses_version_like_tag_filters() -> None:
    """Release workflow should only trigger on version-like tags."""
    assert "- 'v*'" in GITHUB_RELEASE_YAML
    assert "[0-9]*.[0-9]*.[0-9]*" in GITHUB_RELEASE_YAML
    assert "- '*'" not in GITHUB_RELEASE_YAML


def test_release_workflow_template_uses_valid_bump_choice_input() -> None:
    """Release workflow should expose a valid choice input for bump selection."""
    assert "type: choice" in GITHUB_RELEASE_YAML
    assert "options:" in GITHUB_RELEASE_YAML
    for option in (
        "- ''",
        "- 'shame'",
        "- 'default'",
        "- 'proud'",
        "- 'patch'",
        "- 'minor'",
        "- 'major'",
        "- 'alpha'",
        "- 'beta'",
        "- 'rc'",
    ):
        assert option in GITHUB_RELEASE_YAML


def test_action_metadata_uses_local_checkout_install_path() -> None:
    """Action installs from checkout; quality/security is plugin-driven."""
    action_path = Path(__file__).resolve().parents[1] / "action.yml"
    content = action_path.read_text(encoding="utf-8")

    assert 'uv pip install --system -e "$GITHUB_ACTION_PATH"' in content
    assert "run_tests:" not in content
    assert "run_security_scan:" not in content


def test_release_workflow_template_splits_ci_cd_into_subprocess_jobs() -> None:
    """Release workflow should separate PyPI and Docker subprocess jobs."""
    assert "publish_release:" in GITHUB_RELEASE_YAML
    assert "deploy_pypi:" in GITHUB_RELEASE_YAML
    assert "deploy_docker:" in GITHUB_RELEASE_YAML
    assert "quality_and_security:" not in GITHUB_RELEASE_YAML
    assert "needs: [quality_and_security]" not in GITHUB_RELEASE_YAML


def test_release_workflow_template_has_scoped_pyforge_steps() -> None:
    """Each deploy subprocess should run the action in scoped mode."""
    assert "- name: PyForge / PyPI Deploy" in GITHUB_RELEASE_YAML
    assert "- name: PyForge / Docker Deploy" in GITHUB_RELEASE_YAML


def test_release_workflow_template_exposes_plugin_timeout_input() -> None:
    """Workflow template should expose plugin hook timeout input."""
    assert "plugin_timeout_seconds:" in GITHUB_RELEASE_YAML
    assert "Per-hook timeout in seconds" in GITHUB_RELEASE_YAML


def test_action_metadata_exports_plugin_timeout_env() -> None:
    """Composite action should export plugin timeout env variable for CLI hooks."""
    action_path = Path(__file__).resolve().parents[1] / "action.yml"
    content = action_path.read_text(encoding="utf-8")

    assert "plugin_timeout_seconds:" in content
    assert "PYFORGE_PLUGIN_TIMEOUT_SECONDS" in content


def test_release_workflow_template_publishes_github_release_from_changelog() -> None:
    """Release workflow should create GitHub Releases from generated changelog."""
    assert "Publish / GitHub Release" in GITHUB_RELEASE_YAML
    assert "softprops/action-gh-release@v2" in GITHUB_RELEASE_YAML
    assert "Extract Release Notes from CHANGELOG.md" in GITHUB_RELEASE_YAML
    assert "body_path:" in GITHUB_RELEASE_YAML
