# pyforge-deploy

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![PyPI Version](https://img.shields.io/pypi/v/pyforge-deploy?logo=pypi&logoColor=white)](https://pypi.org/project/pyforge-deploy/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/pyforge-deploy?logo=pypi&logoColor=white)](https://pypi.org/project/pyforge-deploy/)
[![Tests](https://img.shields.io/github/actions/workflow/status/ertanturk/pyforge-deploy/test-coverage.yml?branch=main&label=tests&logo=githubactions&logoColor=white)](https://github.com/ertanturk/pyforge-deploy/actions/workflows/test-coverage.yml)

**pyforge-deploy** is a lightweight CLI that automates the Python release pipeline.

It simplifies the transition from **development → distribution** by handling version management, package builds, Docker image creation, PyPI publishing, and CI workflow setup through a single interface.

---

# Why pyforge-deploy?

Publishing Python projects usually involves multiple manual steps:

```
bump version
build package
upload to PyPI
create Docker image
configure CI workflow
```

`pyforge-deploy` automates this workflow so you can release projects consistently and safely.

---

# Features

### Automated Release Workflow

Automates the common Python release pipeline:

```
version → build → publish → docker → CI
```

### Smart Dependency Detection

Automatically detects project dependencies using:

* AST analysis
* `pyproject.toml`
* `requirements.txt`

This information is used to generate production-ready Dockerfiles.

### Version Management

Supports modern Pride-style stable bumps and validates versions against the
latest version on PyPI to avoid conflicts.

Stable bump types:

* `shame` (patch-style)
* `default` (minor-style)
* `proud` (major-style)

Legacy aliases (`patch`, `minor`, `major`) and pre-release bumps (`alpha`,
`beta`, `rc`) are also accepted.

### PyPI Deployment

Builds source and wheel distributions and securely publishes them to:

* PyPI
* TestPyPI

### Docker Integration

Automatically generates a Dockerfile tailored to your project and builds the
image using detected dependencies and Python version.

Docker build flow includes:

* entry-point auto-detection with `src/` path normalization
* optional wheelhouse acceleration with safe fallback behavior
* multi-stage dependency sync for reliable runtime imports
* CI-aware multi-platform handling

### GitHub Actions Integration

Generate a ready-to-use CI/CD workflow for automated releases with a single command.

### Intelligent Command Hooks (Plugins)

Define custom shell commands in `pyproject.toml` and let `pyforge-deploy`
run them at lifecycle stages:

* `before_build` / `after_build`
* `before_release` / `after_release`

Legacy aliases are still supported (`pre_build`, `post_build`, `pre_deploy`,
`post_deploy`).

If no hooks are defined, `pyforge-deploy` skips plugin execution and proceeds
directly with build/deploy (zero-config behavior).

---

# Installation

Install from PyPI:

```bash
pip install pyforge-deploy
```

Docker must be installed and running for Docker-related features.

---

# Quickstart

Initialize release automation for your project:

```bash
pyforge-deploy init
```

Build and publish a new release:

```bash
pyforge-deploy deploy-pypi --bump shame
```

Build a Docker image for the project:

```bash
pyforge-deploy docker-build
```

Command alias:

```bash
pyforge-deploy docker
```

---

# Usage

View all available commands:

```bash
pyforge-deploy --help
```

## Initialize GitHub Workflow

Generate a CI/CD workflow file in your repository:

```bash
pyforge-deploy init
```

This creates:

```
.github/workflows/pyforge-deploy.yml
```

---

## Build a Docker Image

Automatically detect project dependencies and build an image.

```bash
pyforge-deploy docker-build
```

Specify entry point and image tag:

```bash
pyforge-deploy docker-build \
  --entry-point src/pyforge_deploy/cli.py \
  --image-tag my-app:1.0.0
```

---

## Deploy to PyPI

Build and publish a release.

Bump patch version automatically:

```bash
pyforge-deploy deploy-pypi --bump shame
```

Use Pride-style stable bumps:

```bash
pyforge-deploy deploy-pypi --bump default
pyforge-deploy deploy-pypi --bump proud
```

Publish a specific version to TestPyPI:

```bash
pyforge-deploy deploy-pypi --version 2.1.0 --test
```

---

## Inspect Project

View detected dependencies:

```bash
pyforge-deploy show-deps
```

Check current project version:

```bash
pyforge-deploy show-version
```

Check release readiness:

```bash
pyforge-deploy status
```

See auto-detected entry point candidates:

```bash
pyforge-deploy show-entry-point
```

---

# Configuration

## Publishing (OIDC-first)

pyforge-deploy prefers GitHub OIDC (Passwordless / Trusted Publishing) in CI
environments: when running inside GitHub Actions with `id-token: write`
permissions, the action can mint short-lived PyPI tokens so you do NOT need to
store `PYPI_TOKEN` as a repository secret. This is the recommended and secure
default for automated releases.

Locally (or outside OIDC-capable CI) you may still provide a static token. To
use a token locally, set it via a `.env` file or environment variable:

```
PYPI_TOKEN=pypi-your-token-here
```

Use `PYPI_TOKEN` only for local/manual runs; in CI prefer OIDC/trusted
publishing so secrets are not stored long-term.

## pyproject.toml configuration

`pyforge-deploy` reads settings from the `[tool.pyforge-deploy]` table in
`pyproject.toml`. CLI arguments override values in `pyproject.toml`, which in
turn override environment variables and built-in defaults. Example configuration:

```toml
[tool.pyforge-deploy]
default_bump = "shame"          # default bump when releasing
docker_push = true               # whether docker-build should push by default
docker_platforms = "linux/amd64" # platforms for buildx (comma-separated)
auto_confirm = true              # skip interactive prompts
docker_image = "myorg/myapp:latest" # default image tag
docker_python = "3.12"         # override python base image (short form '3.12')
docker_wheelhouse = false        # build a local wheelhouse for Docker builds
docker_non_root = false          # install into non-root user in final image
pypi_retries = 3                 # upload retry attempts
pypi_backoff = 2                 # backoff base seconds for retries
plugin_timeout = 300             # per-hook command timeout in seconds

[tool.pyforge-deploy.plugins]
# release/build hooks (string or list of strings)
before_release = [
  "ruff check .",
  "pytest -q",
]
after_release = [
  "echo Release complete",
]
before_build = [
  "python -m pip check",
]
after_build = []
```

Not all keys are required — the CLI will fall back to sensible defaults when a
setting is omitted. See `src/pyforge_deploy/builders` for how each option is
used at runtime.

## Plugin hook behavior

Hook execution is best-effort by design:

* Commands run with shell execution.
* Non-zero exit, missing executable, and timeout errors are logged as warnings.
* Main Docker/PyPI pipeline continues (does not crash).
* Successful hook output is shown in verbose mode; failure output is shown to aid debugging.

Hook context environment variables are injected for each command:

* `PYFORGE_HOOK_STAGE`
* `PYFORGE_HOOK_COMMAND_INDEX`
* `PYFORGE_HOOK_COMMAND`

CI hook timeout can be overridden with:

* `PYFORGE_PLUGIN_TIMEOUT_SECONDS`

## Plugin recipes (copy/paste)

### 1) Lint + test before PyPI release

```toml
[tool.pyforge-deploy.plugins]
before_release = [
  "ruff check .",
  "pytest -q",
]
```

### 2) Security scan before release

```toml
[tool.pyforge-deploy.plugins]
before_release = [
  "bandit -r src/ --exclude tests/",
]
```

### 3) Type-check + dependency health before Docker build

```toml
[tool.pyforge-deploy.plugins]
before_build = [
  "mypy src/",
  "python -m pip check",
]
```

### 4) Post-release notification hook

```toml
[tool.pyforge-deploy.plugins]
after_release = [
  "echo '[pyforge] release completed'",
]
```

### 5) Backward-compatible legacy stage names

```toml
[tool.pyforge-deploy.plugins]
pre_build = ["ruff check ."]
post_deploy = ["echo done"]
```

Tip: prefer canonical stage names (`before_build`, `after_build`,
`before_release`, `after_release`) for new projects.

---

# GitHub Action

`pyforge-deploy` includes a reusable GitHub Action for automated releases.

After running:

```bash
pyforge-deploy init
```

A workflow file will be generated.

Example workflow (OIDC-enabled template produced by `pyforge-deploy init`):

```yaml
name: PyForge Release

on:
  push:
    tags:
      - 'v*'
      - '[0-9]*.[0-9]*.[0-9]*'
  workflow_dispatch:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: write
  id-token: write

jobs:
  deploy_pypi:
    name: Deploy / PyPI
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v5

      - name: PyForge / PyPI Deploy
        uses: ertanturk/pyforge-deploy@main
        with:
          pypi_deploy: 'true'
          docker_build: 'false'
          bump: 'shame'
          plugin_timeout_seconds: '300'
          target_branch: ${{ github.event.repository.default_branch }}
        env:
          PYFORGE_JSON_LOGS: '1'

  deploy_docker:
    name: Deploy / Docker
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v5

      - name: PyForge / Docker Deploy
        uses: ertanturk/pyforge-deploy@main
        with:
          pypi_deploy: 'false'
          docker_build: 'true'
          docker_platforms: 'linux/amd64,linux/arm64'
          plugin_timeout_seconds: '300'
          target_branch: ${{ github.event.repository.default_branch }}
        env:
          PYFORGE_JSON_LOGS: '1'
          DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
          DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
```

This template enables GitHub OIDC (`id-token: write`) so PyPI tokens can be
minted dynamically during the workflow. You only need to provide Docker
credentials as secrets if you build/push images.

Quality/security/lint/test steps are intentionally plugin-driven. Add them under
`[tool.pyforge-deploy.plugins]` in your project and run them in categorized
hook stages (for example in `before_release` or `before_build`).

---

# Architecture

The tool is structured into modular components.

### VersionEngine

Responsible for resolving and updating project versions.

Sources include:

* `pyproject.toml`
* `.pyforge-deploy-cache/version_cache`

It also fetches the latest version from PyPI to prevent version conflicts.

---

### DockerBuilder

`DockerBuilder` detects project dependencies and Python version, renders a
`Dockerfile` using a Jinja2 template, and builds the Docker image. It implements
several optimizations to produce small, cache-friendly images:

- Multi-stage builds to keep the final image minimal
- BuildKit-aware commands and `--mount=type=cache` usage for pip caching
- Layer caching via careful ordering of dependency installation
- Heavy-hitter detection (large packages like `numpy`, `pandas`) and
  separation into `heavy-requirements.txt` so they can be installed in a
  dedicated layer for better cache reuse
- Optional local wheelhouse (`wheels/`) build to enable `--no-index` installs
  and reproducible builds
- Automatic `.dockerignore` tuning to reduce build context size
- Runtime dependency sync to prevent missing-module errors in final container
- `src/` project entry-point normalization for correct `CMD` path resolution

These features make Docker builds faster, more deterministic, and more
cache-efficient.

---

### PyPIDistributor

Handles package distribution:

1. Cleans old build artifacts
2. Builds source and wheel distributions
3. Uploads them to PyPI or TestPyPI. When `uv` is available on the system, the
  distributor uses `uv build` and `uv publish` (ultra-fast) for building and
  publishing, otherwise it falls back to `python -m build` and `twine upload`.

Publishing in CI prefers OIDC-based short-lived tokens; for local/manual runs
`PYPI_TOKEN` is still supported.

---

# License

This project is licensed under the MIT License.

See the [LICENSE](LICENSE) file for details.
