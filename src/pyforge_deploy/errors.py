"""Custom exceptions for pyforge-deploy.

Provide structured exception types for CLI-friendly error handling.
"""


class PyForgeError(Exception):
    """Base exception for pyforge-deploy."""


class ConfigError(ValueError, PyForgeError):
    """Configuration related errors (pyproject, invalid tags, etc.)."""


class DockerBuildError(RuntimeError, PyForgeError):
    """Errors raised during Docker build/push operations."""


class PyPIDeployError(RuntimeError, PyForgeError):
    """Errors raised during PyPI deployment."""


class DependencyError(RuntimeError, PyForgeError):
    """Errors raised during dependency detection or requirements generation."""


class ValidationError(ValueError, PyForgeError):
    """General validation errors."""
