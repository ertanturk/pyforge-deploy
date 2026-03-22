# pyforge-deploy

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![PyPI Version](https://img.shields.io/pypi/v/pyforge-deploy?logo=pypi&logoColor=white)](https://pypi.org/project/pyforge-deploy/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/pyforge-deploy?logo=pypi&logoColor=white)](https://pypi.org/project/pyforge-deploy/)
[![Tests](https://img.shields.io/github/actions/workflow/status/ertanturk/pyforge-deploy/test-coverage.yml?branch=main&label=tests&logo=githubactions&logoColor=white)](https://github.com/ertanturk/pyforge-deploy/actions/workflows/test-coverage.yml)

  **From messy commits to clean releases in one command.**

`pyforge-deploy` is now focused on a single, opinionated flow:

```bash
pyforge release
```

It analyzes commits, suggests the next version, generates a clean changelog,
asks for confirmation, and finalizes the release.

---

# Why this change?

Release automation tools often become noisy and over-configured.
`pyforge-deploy` now optimizes for one thing: a reliable release experience with sensible defaults.

## Before / After

### Before

```text
read commits manually
guess bump level
write changelog by hand
tag and publish with multiple commands
```

### After

```text
pyforge release
```

---

# Core flow (`pyforge release`)

1. Detect latest tag (or initial release)
2. Collect commits since the last release
3. Analyze commits (Conventional → heuristic → AI fallback)
4. Suggest next version
5. Generate changelog preview
6. Ask for confirmation
7. Update version + changelog + git commit/tag
8. Publish via CI by default (local publish is optional)

Release intelligence highlights in the current codebase:

* **Pre-1.0 SemVer scaling:** when current version is `0.x.y`, breaking-change
  signals are downgraded from MAJOR to MINOR to match SemVer initial-development
  expectations.
* **Schema migration risk detection:** migration paths (`migrations/`, `alembic/`,
  `versions/`) are recognized, and destructive operations (for example
  `DROP TABLE`, `op.drop_column`) add high-impact risk scoring.
* **Security hotfix override:** commits mentioning CVE/GHSA/security/hotfix terms
  receive dominant PATCH confidence to avoid low-confidence interactive blocking.
* **Deterministic historical density scoring:** file-size denominator is resolved
  from git blobs at the analyzed commit revision (not the live working tree).
* **Timeout-safe git collection:** per-commit changed-file and diff collection now
  fails closed on subprocess timeout/errors instead of crashing release planning.

---

# Installation

Install from PyPI:

```bash
pip install pyforge-deploy
```

Docker must be installed and running for Docker-related features.

---

# Quickstart

```bash
pyforge release
```

Example output:

```text
Analyzing commits since v1.2.0...

Detected changes:
- feat: add authentication -> MINOR
- fix: login bug -> PATCH

Suggested version: 1.3.0

Generated changelog:
---
## v1.3.0
- Added
  - add authentication
- Fixed
  - login bug
---

Continue? (y/n)
```

---

# Usage

Use `pyforge release` for normal releases.

Legacy commands still work, but are deprecated and now considered advanced usage.

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

Generate release changelog intelligence (dry-run preview):

```bash
pyforge-deploy release --dry-run
```

Generate release changelog and trigger CI-managed release publish:

```bash
pyforge-deploy release
```

Release command behavior (default):

1. Generates/updates `CHANGELOG.md` with the new release section
2. Verifies changelog exists in the project root
3. Finalizes release git operations (commit changelog + push canonical tag `vX.Y.Z`)
4. Exits after handing off publish work to the tag-triggered GitHub Actions workflow

Local publish mode (explicit opt-in):

```bash
pyforge-deploy release --local-publish
```

When `--local-publish` is enabled, the same command also performs:

* PyPI build + publish
* Docker build + push
* GitHub Release publication from the matching changelog section

If `GITHUB_TOKEN`/`GH_TOKEN` is unavailable locally, GitHub Release publishing is skipped.

If you intentionally need to release from a non-clean working tree:

```bash
pyforge-deploy release --allow-dirty
```

Equivalent environment override:

```
PYFORGE_RELEASE_ALLOW_DIRTY=1
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

[tool.pyforge-deploy.changelog]
custom_prompt = "Generate release notes in English with concise bullets." # optional

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

### AI Router environment variables

Set one or more of the following keys (first match is used):

* `OPENAI_API_KEY`
* `ANTHROPIC_API_KEY`
* `GEMINI_API_KEY`

Optional overrides:

* `OPENAI_BASE_URL` (OpenAI-compatible local/self-hosted endpoint)
* `OPENAI_MODEL` (default: `gpt-4o-mini`)
* `ANTHROPIC_MODEL` (default: `claude-3-5-haiku-latest`)
* `PYFORGE_AI_PROVIDER` (force provider selection)
* `PYFORGE_AI_API_KEY` (shared key for selected provider)
* `PYFORGE_AI_BASE_URL` (preferred OpenAI-compatible base URL override)

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

Version resolution behavior highlights:

* dry-run still fetches latest PyPI version (read-only) for realistic previews
* static `pyproject.toml` versions are used by default unless explicit bump intent exists
* explicit bump intent (`--bump` / auto-increment) applies against the best available base version
* missing PyPI package (404) is treated as initial-release information

---

### ChangelogEngine

`ChangelogEngine` provides release-note intelligence and changelog automation.

Core behavior:

* extracts commits since latest release reference
* parses commits in parallel for speed
* applies strict Conventional Commit rules and fuzzy fallback heuristics
* routes malformed commits to AI providers (OpenAI/Anthropic/Gemini)
* chunks large commit histories and merges markdown outputs safely
* supports user-defined prompt override via `[tool.pyforge-deploy.changelog]`

`CommitAnalyzer` behavior used by `pyforge release` also includes:

* confidence-gated bump decisions with AI-assisted low-confidence fallback
* release-noise filtering (`chore(release): ...`, merge noise, trivial typo/WIP)
* blast-radius + structural + dependency + deprecation + migration scoring layers

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
