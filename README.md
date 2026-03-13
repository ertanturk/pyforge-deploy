# pyforge-deploy

> **Note:** This is a personal/educational project. It is not intended to compete with established
> tools

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

`pyforge-deploy` is a lightweight automation CLI for Python projects, designed to streamline the transition from development to distribution. It automates Docker image creation, version management, PyPI publishing, and GitHub Actions integration with a simple, intelligent interface.

## Features

*   **Docker Integration:** Automatically creates a project-specific `Dockerfile` by detecting the required Python version and project dependencies through AST analysis, `pyproject.toml`, or `requirements.txt`.
*   **Version Management:** Increments your project's version (patch, minor, or major) and safely validates it against the latest release on PyPI to prevent conflicts.
*   **PyPI Deployment:** Builds and uploads your project's source and wheel distributions to PyPI or TestPyPI using secure token authentication.
*   **GitHub Action Integration:** Provides a dedicated GitHub Action and an `init` command to set up a complete CI/CD workflow file in your repository instantly.
*   **CLI Commands:** A straightforward command-line interface for initializing workflows, building images, deploying packages, and inspecting project configurations.

## Installation

The tool is available on PyPI:

```bash
pip install pyforge-deploy
```

**Note:** Docker must be installed and running on your system to use Docker-related features.

## Usage

Get a full list of commands and options with:
```bash
pyforge-deploy --help
```

### Initialize a GitHub Workflow
Generate a `.github/workflows/pyforge-deploy.yml` file in your repository to automate releases.
```bash
pyforge-deploy init
```

### Build a Docker Image
Generate a Dockerfile and build an image. `pyforge-deploy` will auto-detect the image tag from your project version and DockerHub username (if `DOCKERHUB_USERNAME` is set as an environment variable).

```bash
# Auto-detects entry point and image tag
pyforge-deploy docker-build

# Specify an entry point and tag
pyforge-deploy docker-build --entry-point src/pyforge_deploy/cli.py --image-tag my-app:1.0.0
```

### Deploy to PyPI
Bump the version, build, and publish your package to PyPI.

```bash
# Bump the patch version and deploy to PyPI
pyforge-deploy deploy-pypi --bump patch

# Deploy a specific version to TestPyPI for validation
pyforge-deploy deploy-pypi --version 2.1.0 --test
```

### Inspect Project
Quickly view detected dependencies or the current project version.

```bash
# Show project dependencies
pyforge-deploy show-deps

# Show current project version
pyforge-deploy show-version
```

## Configuration

### PyPI Token
For publishing packages to PyPI or TestPyPI, an API token is required. You can provide it by creating a `.env` file in your project's root directory:

```
PYPI_TOKEN=pypi-your-token-here
```
Alternatively, you can export `PYPI_TOKEN` as an environment variable in your shell or CI/CD system.

## GitHub Action

This repository provides a reusable GitHub Action to automate your release process. After running `pyforge-deploy init`, a workflow file will be created.

Here is an example of `pyforge-deploy.yml`:

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

To use this, you must add `PYPI_TOKEN`, `DOCKERHUB_USERNAME`, and `DOCKERHUB_TOKEN` to your repository's secrets under **Settings > Secrets and variables > Actions**.

## How It Works

*   **Version Engine:** The `VersionEngine` resolves the project version from `pyproject.toml`, `__about__.py`, or a local `.version_cache`. It fetches the latest version from PyPI to avoid conflicts, calculates the next version based on your input, and writes the updated version back to `src/<package_name>/__about__.py` and `.version_cache`.
*   **Docker Builder:** The `DockerBuilder` detects project dependencies and the Python version. It uses this information to render a `Dockerfile.j2` template, creating a production-ready `Dockerfile`. It then invokes the Docker engine to build the image.
*   **PyPI Distributor:** The `PyPIDistributor` first cleans any old build artifacts. It then uses the `build` package to create source and wheel distributions and `twine` to securely upload them to the specified repository (PyPI or TestPyPI).

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.