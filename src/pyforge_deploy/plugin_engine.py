"""Plugin hook execution engine for user-defined CI/CD shell commands."""

from __future__ import annotations

import os
import subprocess  # nosec B404
from collections.abc import Iterable

from pyforge_deploy.config import get_plugins_config, resolve_setting
from pyforge_deploy.logutil import log

# Canonical, professional stage names
HOOK_STAGE_ALIASES: dict[str, str] = {
    "before_build": "before_build",
    "after_build": "after_build",
    "before_release": "before_release",
    "after_release": "after_release",
    # Backward-compatible aliases
    "pre_build": "before_build",
    "post_build": "after_build",
    "pre_deploy": "before_release",
    "post_deploy": "after_release",
}


def _normalize_stage(stage: str) -> str:
    """Normalize user-provided stage name to canonical form."""
    key = stage.strip().lower()
    return HOOK_STAGE_ALIASES.get(key, key)


def _as_command_list(value: object) -> list[str]:
    """Convert hook command config value into a normalized command list."""
    if isinstance(value, str):
        cmd = value.strip()
        return [cmd] if cmd else []
    if isinstance(value, list):
        commands: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                commands.append(item.strip())
        return commands
    return []


def _get_stage_commands(stage: str) -> list[str]:
    """Read hook command list for a stage from pyproject configuration."""
    plugins = get_plugins_config()
    if not plugins:
        return []

    commands: list[str] = []
    canonical = _normalize_stage(stage)

    # Support both canonical and legacy keys in the plugins table.
    # Preserve deterministic key priority for stable hook execution order:
    # canonical -> requested key -> legacy aliases (definition order).
    candidate_keys: list[str] = []

    def _append_key(key: str) -> None:
        if key and key not in candidate_keys:
            candidate_keys.append(key)

    _append_key(canonical)
    _append_key(stage.strip().lower())
    for alias_key, alias_target in HOOK_STAGE_ALIASES.items():
        if alias_target == canonical:
            _append_key(alias_key)

    for key in candidate_keys:
        commands.extend(_as_command_list(plugins.get(key)))

    # Preserve order while removing duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for command in commands:
        if command not in seen:
            seen.add(command)
            unique.append(command)
    return unique


def _resolve_timeout_seconds(timeout_seconds: int | None) -> int:
    """Resolve plugin timeout from explicit arg or project configuration."""
    if timeout_seconds is not None:
        return max(1, int(timeout_seconds))

    resolved = resolve_setting(
        None,
        "plugin_timeout",
        env_keys=("PYFORGE_PLUGIN_TIMEOUT_SECONDS",),
        default=300,
        cast=int,
    )
    try:
        return max(1, int(resolved))
    except (TypeError, ValueError):
        return 300


def run_hooks(
    stage: str,
    *,
    verbose: bool = False,
    timeout_seconds: int | None = None,
) -> None:
    """Execute configured shell hooks for a stage without breaking pipeline flow.

    This function is intentionally best-effort: all errors are downgraded to
    warnings so deployment continues.

    Args:
        stage: Hook stage name (canonical or alias).
        verbose: When True, print successful hook output.
        timeout_seconds: Optional per-command timeout override.
    """
    normalized_stage = _normalize_stage(stage)
    commands = _get_stage_commands(stage)
    if not commands:
        return

    timeout = _resolve_timeout_seconds(timeout_seconds)
    for index, command in enumerate(commands, start=1):
        try:
            env = os.environ.copy()
            env["PYFORGE_HOOK_STAGE"] = normalized_stage
            env["PYFORGE_HOOK_COMMAND_INDEX"] = str(index)
            env["PYFORGE_HOOK_COMMAND"] = command

            result = subprocess.run(  # nosec B602
                command,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )

            if result.returncode != 0:
                log(
                    (
                        f"Hook '{command}' failed at stage '{normalized_stage}' "
                        f"with exit code {result.returncode}. Skipping."
                    ),
                    level="warning",
                    color="yellow",
                    component="PluginEngine",
                )
                error_output = (result.stderr or result.stdout or "").strip()
                if error_output:
                    log(
                        f"Hook error output: {error_output}",
                        level="warning",
                        color="yellow",
                        component="PluginEngine",
                    )
                continue

            if verbose:
                output = (result.stdout or "").strip()
                if output:
                    log(
                        (
                            f"Hook output at stage '{normalized_stage}' "
                            f"for '{command}':\n{output}"
                        ),
                        level="info",
                        color="gray",
                        component="PluginEngine",
                    )

        except subprocess.TimeoutExpired:
            log(
                (
                    f"Hook '{command}' timed out after {timeout}s at stage "
                    f"'{normalized_stage}'. Skipping."
                ),
                level="warning",
                color="yellow",
                component="PluginEngine",
            )
            continue
        except FileNotFoundError as exc:
            log(
                (
                    f"Hook '{command}' executable/shell not found at stage "
                    f"'{normalized_stage}': {exc}. Skipping."
                ),
                level="warning",
                color="yellow",
                component="PluginEngine",
            )
            continue
        except Exception as exc:
            log(
                (
                    f"Hook '{command}' raised unexpected error at stage "
                    f"'{normalized_stage}': {exc}. Skipping."
                ),
                level="warning",
                color="yellow",
                component="PluginEngine",
            )
            continue


def list_supported_hook_stages() -> Iterable[str]:
    """Return canonical hook stages in execution lifecycle order."""
    return (
        "before_build",
        "after_build",
        "before_release",
        "after_release",
    )
