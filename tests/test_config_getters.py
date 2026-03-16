from pytest import MonkeyPatch

from pyforge_deploy.config import get_bool_setting, get_int_setting, get_list_setting


def test_get_bool_setting_from_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PYFORGE_JSON_LOGS", "true")
    assert get_bool_setting(
        None, "nonexistent", env_keys=("PYFORGE_JSON_LOGS",), default=False
    )


def test_get_int_setting_and_list(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PYFORGE_PYPI_RETRIES", "4")
    assert (
        get_int_setting(
            None, "pypi_retries", env_keys=("PYFORGE_PYPI_RETRIES",), default=3
        )
        == 3
    )

    monkeypatch.setenv("PYFORGE_LIST", "a,b,c")
    assert get_list_setting(None, "lst", env_keys=("PYFORGE_LIST",)) == ["a", "b", "c"]
