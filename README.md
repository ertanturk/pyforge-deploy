# pyforge-deploy

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![PyPI - Version](https://img.shields.io/pypi/v/pyforge-deploy)
![PyPI - Downloads](https://img.shields.io/pypi/dm/pyforge-deploy)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/ertanturk/pyforge-deploy/pyforge-deploy.yml)

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

Safely increments project versions (`patch`, `minor`, `major`) and validates them against the latest version on PyPI to avoid conflicts.

### PyPI Deployment

Builds source and wheel distributions and securely publishes them to:

* PyPI
* TestPyPI

### Docker Integration

Automatically generates a Dockerfile tailored to your project and builds the image using the detected dependencies and Python version.

### GitHub Actions Integration

Generate a ready-to-use CI/CD workflow for automated releases with a single command.

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
pyforge-deploy deploy-pypi --bump patch
```

Build a Docker image for the project:

```bash
pyforge-deploy docker-build
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
pyforge-deploy deploy-pypi --bump patch
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

---

# Configuration

## PyPI Token

Publishing to PyPI requires an API token.

Create a `.env` file in your project root:

```
PYPI_TOKEN=pypi-your-token-here
```

Or export it as an environment variable:

```
export PYPI_TOKEN=pypi-your-token-here
```

---

# GitHub Action

`pyforge-deploy` includes a reusable GitHub Action for automated releases.

After running:

```bash
pyforge-deploy init
```

A workflow file will be generated.

Example workflow:

```yaml
name: PyForge Release

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  release:
    name: Build and Publish
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Code
        uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - name: PyForge Deploy
        uses: ertanturk/pyforge-deploy@main
        with:
          pypi_deploy: 'true'
          docker_build: 'true'
          bump: 'patch'
          target_branch: ${{ github.event.repository.default_branch }}

        env:
          PYPI_TOKEN: ${{ secrets.PYPI_TOKEN }}
          DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
          DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
```

To use this workflow, add the following secrets in your repository:

```
PYPI_TOKEN
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
```

Navigate to:

```
Settings → Secrets and variables → Actions
```

---

# Architecture

The tool is structured into modular components.

### VersionEngine

Responsible for resolving and updating project versions.

Sources include:

* `pyproject.toml`
* `__about__.py`
* `.version_cache`

It also fetches the latest version from PyPI to prevent version conflicts.

---

### DockerBuilder

Detects project dependencies and Python version, renders a `Dockerfile` using a template, and builds the Docker image.

---

### PyPIDistributor

Handles package distribution:

1. Cleans old build artifacts
2. Builds source and wheel distributions
3. Uploads them to PyPI or TestPyPI using `twine`

---

# License

This project is licensed under the MIT License.

See the [LICENSE](LICENSE) file for details.
