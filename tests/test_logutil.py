import json

import pytest

from pyforge_deploy.logutil import log


def test_logutil_json_mode(
    monkeypatch: "pytest.MonkeyPatch", capsys: "pytest.CaptureFixture[str]"
) -> None:
    monkeypatch.setenv("PYFORGE_JSON_LOGS", "1")
    log("hello world", level="debug")
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["level"] == "debug"
    assert "hello world" in data["message"]
