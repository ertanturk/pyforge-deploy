import os
from collections.abc import Callable, Iterable
from typing import Any

from pyforge_deploy.builders.version_engine import get_tool_config


def resolve_setting(
    cli_value: Any,
    tool_key: str,
    env_keys: Iterable[str] | None = None,
    default: Any = None,
    cast: Callable[[Any], Any] | None = None,
) -> Any:
    """Resolve a setting with priority:
    CLI -> pyproject [tool.pyforge-deploy] -> env vars -> default.

    - cli_value: value provided by CLI (may be None)
    - tool_key: key to look up in get_tool_config()
    - env_keys: list of environment variable names to check
    - default: fallback default
    - cast: optional callable to cast returned value
    """

    if cli_value is not None:
        if cast and not isinstance(cli_value, bool):
            try:
                return cast(cli_value)
            except Exception:
                return cli_value
        return cli_value

    try:
        tool_conf = get_tool_config()
        if tool_conf and tool_key in tool_conf:
            val = tool_conf.get(tool_key)
            if cast and not isinstance(val, bool):
                try:
                    return cast(val)
                except Exception:
                    return val
            return val
    except Exception as e:
        import sys

        sys.stderr.write(f"[pyforge-deploy] get_tool_config failed: {e}\n")
        return default

    if env_keys:
        for k in env_keys:
            v = os.environ.get(k)
            if v is not None:
                if cast:
                    try:
                        return cast(v)
                    except Exception:
                        return v
                return v

    return default


def get_bool_setting(
    cli_value: Any,
    tool_key: str,
    env_keys: Iterable[str] | None = None,
    default: bool = False,
) -> bool:
    val = resolve_setting(cli_value, tool_key, env_keys=env_keys, default=default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in {"1", "true", "yes", "on"}
    try:
        return bool(val)
    except Exception:
        return default


def get_int_setting(
    cli_value: Any,
    tool_key: str,
    env_keys: Iterable[str] | None = None,
    default: int = 0,
) -> int:
    val = resolve_setting(cli_value, tool_key, env_keys=env_keys, default=default)
    try:
        return int(val)
    except Exception:
        return default


def get_list_setting(
    cli_value: Any,
    tool_key: str,
    env_keys: Iterable[str] | None = None,
    default: list[str] | None = None,
) -> list[str]:
    default = default or []
    val = resolve_setting(cli_value, tool_key, env_keys=env_keys, default=default)
    if isinstance(val, list):
        return [str(x) for x in val]
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return default
