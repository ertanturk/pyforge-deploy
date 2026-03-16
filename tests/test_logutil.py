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


def test_status_bar_plain_output(capsys: "pytest.CaptureFixture[str]") -> None:
    """Status bar should include percentage and step info in plain mode."""
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
