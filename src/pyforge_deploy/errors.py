"""Custom exceptions for pyforge-deploy.

Provide structured exception types for CLI-friendly error handling.
Organized by functional domain for granular debugging.
"""


class PyForgeError(Exception):
    """Base exception for pyforge-deploy."""


# Configuration & Validation Errors
class ConfigError(ValueError, PyForgeError):
    """Configuration related errors (pyproject, invalid tags, etc.)."""


class ValidationError(ValueError, PyForgeError):
    """General validation errors (invalid input, malformed data)."""


# File & I/O Errors
class FileError(OSError, PyForgeError):
    """File I/O errors (read/write failures, missing files)."""


class CacheError(FileError):
    """Errors related to cache file operations."""


# Version Management Errors
class VersionError(ValueError, PyForgeError):
    """Version resolution, bumping, or comparison errors."""


class VersionConflictError(VersionError):
    """Version conflict between local and remote (PyPI) versions."""


# Git Integration Errors
class GitError(RuntimeError, PyForgeError):
    """Git command execution or analysis errors."""


class GitNotFoundError(GitError):
    """Git executable not found or not installed."""


# Entry Point Detection Errors
class EntryPointError(RuntimeError, PyForgeError):
    """Entry point detection or validation errors."""


class EntryPointNotFoundError(EntryPointError):
    """No entry point could be detected for the project."""


class EntryPointAmbiguousError(EntryPointError):
    """Multiple potential entry points detected; cannot auto-select."""


# Container & Deployment Errors
class DockerBuildError(RuntimeError, PyForgeError):
    """Errors raised during Docker build/push operations."""


class DockerConfigError(ConfigError, DockerBuildError):
    """Invalid Docker configuration (bad image tag, platform mismatch)."""


class DockerExecutionError(DockerBuildError):
    """Docker build or push command execution failure."""


class PyPIDeployError(RuntimeError, PyForgeError):
    """Errors raised during PyPI deployment."""


class PyPIAuthError(PyPIDeployError):
    """PyPI authentication/token errors."""


class PyPIUploadError(PyPIDeployError):
    """PyPI upload/publish failures."""


class PyPINetworkError(PyPIDeployError):
    """Network errors communicating with PyPI."""


# Dependency & Template Errors
class DependencyError(RuntimeError, PyForgeError):
    """Errors raised during dependency detection or requirements generation."""


class TemplateError(RuntimeError, PyForgeError):
    """Template rendering or processing errors."""


class TemplateRenderError(TemplateError):
    """Jinja2 template rendering failure."""


class TemplateContextError(TemplateError):
    """Missing or invalid template context variables."""


# Network Errors
class NetworkError(RuntimeError, PyForgeError):
    """Network connectivity errors (timeouts, connection refused)."""


class TimeoutError(NetworkError):  # noqa: A001
    """Network operation timeout."""


# Command Execution Errors
class CommandError(RuntimeError, PyForgeError):
    """Subprocess command execution errors."""


class CommandNotFoundError(CommandError):
    """Required command not found in PATH."""


class CommandFailedError(CommandError):
    """Subprocess command exited with non-zero code."""
