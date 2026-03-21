import json

import pytest

from pyforge_deploy.logutil import log, status_bar


def test_logutil_json_mode(
    monkeypatch: "pytest.MonkeyPatch", capsys: "pytest.CaptureFixture[str]"
) -> None:
    monkeypatch.setenv("PYFORGE_JSON_LOGS", "1")
    log("hello world", level="debug")
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["level"] == "debug"
    assert "hello world" in data["message"]
    assert data["event"] == "log"


def test_logutil_json_mode_includes_ci_context(
    monkeypatch: "pytest.MonkeyPatch", capsys: "pytest.CaptureFixture[str]"
) -> None:
    """JSON logs should include CI metadata when running in CI-like envs."""
    monkeypatch.setenv("PYFORGE_JSON_LOGS", "1")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")

    log(
        "release step starting",
        level="info",
        component="CLI",
        event="startup",
        stage="release",
    )
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())

    assert data["ci"] is True
    assert data["component"] == "CLI"
    assert data["event"] == "startup"
    assert data["stage"] == "release"
    assert data["timestamp"].endswith("Z")
    assert data["ci_context"]["provider"] == "github-actions"
    assert data["ci_context"]["run_id"] == "12345"
    assert data["ci_context"]["sha"] == "abc123"


def test_status_bar_plain_output(
    monkeypatch: "pytest.MonkeyPatch", capsys: "pytest.CaptureFixture[str]"
) -> None:
    """Status bar should include percentage and step info in plain mode."""
    monkeypatch.delenv("PYFORGE_JSON_LOGS", raising=False)
    status_bar(2, 4, "Building")
    captured = capsys.readouterr()
    assert "50% (2/4) Building" in captured.out


def test_status_bar_json_mode(
    monkeypatch: "pytest.MonkeyPatch", capsys: "pytest.CaptureFixture[str]"
) -> None:
    """Status bar should emit structured progress payload in JSON mode."""
    monkeypatch.setenv("PYFORGE_JSON_LOGS", "1")
    status_bar(1, 5, "Starting")
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["message"] == "Starting"
    assert data["progress"]["current"] == 1
    assert data["progress"]["total"] == 5
    assert data["progress"]["percent"] == 20
    assert data["event"] == "progress"


def test_log_plain_output_with_component(
    monkeypatch: "pytest.MonkeyPatch", capsys: "pytest.CaptureFixture[str]"
) -> None:
    """Plain logs should include component prefix when provided."""
    monkeypatch.delenv("PYFORGE_JSON_LOGS", raising=False)
    log("started", component="CLI")
    captured = capsys.readouterr()
    assert "[INFO] [CLI] started" in captured.out
