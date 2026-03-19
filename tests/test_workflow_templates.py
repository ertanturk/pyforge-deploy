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
    """Action installs from checkout and runs pytest directly."""
    action_path = Path("/home/ertan/pyforge-deploy/action.yml")
    content = action_path.read_text(encoding="utf-8")

    assert 'uv pip install --system -e "$GITHUB_ACTION_PATH"' in content
    assert "python -m pytest" in content
