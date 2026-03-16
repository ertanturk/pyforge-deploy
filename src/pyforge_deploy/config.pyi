from collections.abc import Callable, Iterable
from typing import Any

def resolve_setting(
    cli_value: Any,
    tool_key: str,
    env_keys: Iterable[str] | None = ...,
    default: Any = ...,
    cast: Callable[[Any], Any] | None = ...,
) -> Any: ...
def get_bool_setting(
    cli_value: Any,
    tool_key: str,
    env_keys: Iterable[str] | None = ...,
    default: bool = ...,
) -> bool: ...
def get_int_setting(
    cli_value: Any,
    tool_key: str,
    env_keys: Iterable[str] | None = ...,
    default: int = ...,
) -> int: ...
def get_list_setting(
    cli_value: Any,
    tool_key: str,
    env_keys: Iterable[str] | None = ...,
    default: list[str] | None = ...,
) -> list[str]: ...
