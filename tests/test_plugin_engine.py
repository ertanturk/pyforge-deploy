"""Tests for plugin hook engine behavior and resilience."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import pyforge_deploy.plugin_engine as plugin_mod


class _Completed:
    """Small subprocess result stub for tests."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_hooks_returns_quietly_when_no_plugin_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No config should skip hook execution without warnings."""
    monkeypatch.setattr(plugin_mod, "get_plugins_config", lambda: {})

    calls: list[str] = []

    def fake_run(*args: object, **kwargs: object) -> _Completed:
        calls.append("called")
        return _Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    plugin_mod.run_hooks("before_build")
    assert calls == []


def test_run_hooks_executes_legacy_alias_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy stage keys should still run when canonical stage is requested."""
    monkeypatch.setattr(
        plugin_mod, "get_plugins_config", lambda: {"pre_build": ["echo ok"]}
    )

    seen_commands: list[str] = []

    def fake_run(command: str, **kwargs: object) -> _Completed:
        seen_commands.append(command)
        return _Completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    plugin_mod.run_hooks("before_build")
    assert seen_commands == ["echo ok"]


def test_get_stage_commands_preserves_deterministic_priority_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical stage commands should run before alias-derived commands."""
    monkeypatch.setattr(
        plugin_mod,
        "get_plugins_config",
        lambda: {
            "before_build": ["echo canonical"],
            "pre_build": ["echo alias"],
        },
    )

    assert plugin_mod._get_stage_commands("before_build") == [
        "echo canonical",
        "echo alias",
    ]


def test_run_hooks_logs_success_output_only_in_verbose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful stdout should be emitted only when verbose mode is on."""
    monkeypatch.setattr(
        plugin_mod,
        "get_plugins_config",
        lambda: {"before_build": ["echo done"]},
    )

    logs: list[dict[str, Any]] = []

    def fake_log(message: str, **kwargs: Any) -> None:
        logs.append({"message": message, **kwargs})

    monkeypatch.setattr(plugin_mod, "log", fake_log)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _Completed(returncode=0, stdout="build-complete"),
    )

    plugin_mod.run_hooks("before_build", verbose=False)
    assert not logs

    plugin_mod.run_hooks("before_build", verbose=True)
    assert any("build-complete" in item["message"] for item in logs)


def test_run_hooks_warns_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failing command should emit warning and include error output."""
    monkeypatch.setattr(
        plugin_mod, "get_plugins_config", lambda: {"after_build": ["badcmd"]}
    )

    logs: list[str] = []

    def fake_log(message: str, **kwargs: Any) -> None:
        logs.append(message)

    monkeypatch.setattr(plugin_mod, "log", fake_log)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _Completed(returncode=2, stderr="boom"),
    )

    plugin_mod.run_hooks("after_build")

    assert any("Hook 'badcmd' failed" in m for m in logs)
    assert any("boom" in m for m in logs)


def test_run_hooks_warns_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeouts must be downgraded to warnings and never raised."""
    monkeypatch.setattr(
        plugin_mod, "get_plugins_config", lambda: {"before_release": ["sleep 999"]}
    )

    logs: list[str] = []
    monkeypatch.setattr(
        plugin_mod, "log", lambda message, **kwargs: logs.append(message)
    )

    def fake_run(*args: object, **kwargs: object) -> _Completed:
        raise subprocess.TimeoutExpired(cmd="sleep 999", timeout=300)

    monkeypatch.setattr(subprocess, "run", fake_run)

    plugin_mod.run_hooks("before_release")
    assert any("timed out" in m for m in logs)


def test_run_hooks_warns_on_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected exceptions from subprocess must not break deployment flow."""
    monkeypatch.setattr(
        plugin_mod, "get_plugins_config", lambda: {"after_release": ["echo hi"]}
    )

    logs: list[str] = []
    monkeypatch.setattr(
        plugin_mod, "log", lambda message, **kwargs: logs.append(message)
    )

    def fake_run(*args: object, **kwargs: object) -> _Completed:
        raise RuntimeError("bad shell")

    monkeypatch.setattr(subprocess, "run", fake_run)

    plugin_mod.run_hooks("after_release")
    assert any("unexpected error" in m for m in logs)


def test_timeout_resolution_uses_config_and_clamps_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout should resolve from config/env and stay >= 1 second."""
    monkeypatch.setattr(
        plugin_mod,
        "resolve_setting",
        lambda *args, **kwargs: 42,
    )
    assert plugin_mod._resolve_timeout_seconds(None) == 42
    assert plugin_mod._resolve_timeout_seconds(0) == 1


def test_run_hooks_exposes_context_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hook subprocess should receive stage and command context env vars."""
    monkeypatch.setattr(
        plugin_mod,
        "get_plugins_config",
        lambda: {"before_build": ["echo one"]},
    )

    captured_env: dict[str, str] = {}

    def fake_run(command: str, **kwargs: Any) -> _Completed:
        env = kwargs.get("env")
        if isinstance(env, dict):
            captured_env.update(
                {
                    "PYFORGE_HOOK_STAGE": str(env.get("PYFORGE_HOOK_STAGE", "")),
                    "PYFORGE_HOOK_COMMAND_INDEX": str(
                        env.get("PYFORGE_HOOK_COMMAND_INDEX", "")
                    ),
                    "PYFORGE_HOOK_COMMAND": str(env.get("PYFORGE_HOOK_COMMAND", "")),
                }
            )
        return _Completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    plugin_mod.run_hooks("before_build")

    assert captured_env["PYFORGE_HOOK_STAGE"] == "before_build"
    assert captured_env["PYFORGE_HOOK_COMMAND_INDEX"] == "1"
    assert captured_env["PYFORGE_HOOK_COMMAND"] == "echo one"
