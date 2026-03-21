import json
import os
from datetime import UTC, datetime
from typing import Any

from .colors import color_text


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _json_enabled() -> bool:
    return _truthy(os.environ.get("PYFORGE_JSON_LOGS", "false"))


def _is_ci() -> bool:
    return any(
        _truthy(os.environ.get(key))
        for key in (
            "CI",
            "GITHUB_ACTIONS",
            "GITLAB_CI",
            "CIRCLECI",
            "BUILDKITE",
            "TF_BUILD",
        )
    ) or bool(os.environ.get("JENKINS_URL"))


def _ci_provider() -> str | None:
    if _truthy(os.environ.get("GITHUB_ACTIONS")):
        return "github-actions"
    if _truthy(os.environ.get("GITLAB_CI")):
        return "gitlab-ci"
    if _truthy(os.environ.get("CIRCLECI")):
        return "circleci"
    if _truthy(os.environ.get("BUILDKITE")):
        return "buildkite"
    if _truthy(os.environ.get("TF_BUILD")):
        return "azure-pipelines"
    if os.environ.get("JENKINS_URL"):
        return "jenkins"
    return "generic-ci" if _truthy(os.environ.get("CI")) else None


def _timestamp_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ci_context() -> dict[str, str]:
    keys: dict[str, str] = {
        "run_id": os.environ.get("GITHUB_RUN_ID")
        or os.environ.get("CI_PIPELINE_ID")
        or os.environ.get("BUILD_ID")
        or os.environ.get("BUILD_NUMBER")
        or "",
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT") or "",
        "workflow": os.environ.get("GITHUB_WORKFLOW")
        or os.environ.get("CI_PIPELINE_SOURCE")
        or "",
        "sha": os.environ.get("GITHUB_SHA")
        or os.environ.get("CI_COMMIT_SHA")
        or os.environ.get("BUILD_SOURCEVERSION")
        or "",
        "ref": os.environ.get("GITHUB_REF")
        or os.environ.get("CI_COMMIT_REF_NAME")
        or os.environ.get("BRANCH_NAME")
        or "",
        "actor": os.environ.get("GITHUB_ACTOR")
        or os.environ.get("GITLAB_USER_LOGIN")
        or os.environ.get("BUILD_REQUESTEDFOR")
        or "",
    }
    provider = _ci_provider()
    if provider:
        keys["provider"] = provider
    return {k: v for k, v in keys.items() if v}


def _build_payload(
    message: str,
    level: str,
    *,
    component: str | None,
    event: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": _timestamp_utc(),
        "level": level,
        "event": event,
        "message": message,
        "ci": _is_ci(),
    }
    if component:
        payload["component"] = component
    if payload["ci"]:
        ci = _ci_context()
        if ci:
            payload["ci_context"] = ci
    payload.update(extra)
    return payload


def log(
    message: str,
    level: str = "info",
    color: str = "blue",
    *,
    component: str | None = None,
    event: str = "log",
    **fields: Any,
) -> None:
    """Emit a log message. If JSON mode enabled, emit structured JSON.

    Otherwise print colored human-readable output.
    """
    if _json_enabled():
        payload = _build_payload(
            message,
            level,
            component=component,
            event=event,
            extra=fields,
        )
        print(json.dumps(payload))
    else:
        prefix = f"[{level.upper()}] "
        component_prefix = f"[{component}] " if component else ""
        print(color_text(f"{prefix}{component_prefix}{message}", color))


def status_bar(
    current: int,
    total: int,
    message: str,
    *,
    width: int = 24,
    level: str = "info",
    color: str = "cyan",
    component: str | None = None,
    **fields: Any,
) -> None:
    """Emit a status/progress bar message.

    Args:
        current: Current step index.
        total: Total number of steps.
        message: Human-readable status message.
        width: Visual bar width when not in JSON mode.
        level: Log level label.
        color: Color name for non-JSON output.
    """
    safe_total = max(total, 1)
    safe_current = max(0, min(current, safe_total))
    percent = int((safe_current / safe_total) * 100)

    if _json_enabled():
        payload = _build_payload(
            message,
            level,
            component=component,
            event="progress",
            extra=fields,
        )
        payload.update(
            {
                "progress": {
                    "current": safe_current,
                    "total": safe_total,
                    "percent": percent,
                }
            }
        )
        print(json.dumps(payload))
        return

    filled = int((safe_current / safe_total) * width)
    bar = ("#" * filled) + ("-" * (width - filled))
    text = (
        f"[{level.upper()}] [{bar}] {percent:>3}% "
        f"({safe_current}/{safe_total}) {message}"
    )
    if component:
        text = f"[{component}] {text}"
    print(color_text(text, color))
