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
    action_path = Path(__file__).resolve().parents[1] / "action.yml"
    content = action_path.read_text(encoding="utf-8")

    assert 'uv pip install --system -e "$GITHUB_ACTION_PATH"' in content
    assert "python -m pytest" in content


def test_release_workflow_template_splits_ci_cd_into_subprocess_jobs() -> None:
    """Release workflow should separate quality, PyPI, and Docker subprocesses."""
    assert "quality_and_security:" in GITHUB_RELEASE_YAML
    assert "deploy_pypi:" in GITHUB_RELEASE_YAML
    assert "deploy_docker:" in GITHUB_RELEASE_YAML
    assert "needs: [quality_and_security]" in GITHUB_RELEASE_YAML


def test_release_workflow_template_has_scoped_pyforge_steps() -> None:
    """Each workflow subprocess should run the action in scoped mode."""
    assert "- name: PyForge / Quality + Security" in GITHUB_RELEASE_YAML
    assert "pypi_deploy: 'false'" in GITHUB_RELEASE_YAML
    assert "docker_build: 'false'" in GITHUB_RELEASE_YAML
    assert "- name: PyForge / PyPI Deploy" in GITHUB_RELEASE_YAML
    assert "- name: PyForge / Docker Deploy" in GITHUB_RELEASE_YAML
