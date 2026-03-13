# pyforge-deploy

> **Note:** This is a personal/educational project. It is not intended to compete with established
> tools

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)


> **pyforge-deploy** is a lightweight automation CLI for Python projects, streamlining the transition from development to distribution. It automates Docker image creation, version management, and PyPI publishing with a simple, intelligent interface.

---

## Features

* **Docker Integration:** Generates a project-specific Dockerfile from a Jinja2 template and detects the required Python version and dependencies.
* **Version Management:** Increments the project version (patch, minor, major) and verifies the new version is greater than the latest release on PyPI.
* **PyPI Deployment:** Builds source and wheel distributions and uploads them to PyPI or TestPyPI using token authentication.
* **Dependency Detection:** Scans for dependencies using pyproject.toml, requirements.txt, or import analysis.
* **CLI Commands:** Provides commands for building, deploying, and inspecting the project.

---

## Installation

Available on PyPI: https://pypi.org/project/pyforge-deploy/.

```bash
pip install pyforge-deploy
```

> **Note:** Docker must be installed and running for Docker-related features.

---

## Usage

Get a list of all available commands and options:

```bash
pyforge-deploy --help
```

### Common Commands

- **Build a Docker Image:**

  ```bash
  pyforge-deploy docker-build --entry-point src/pyforge_deploy/cli.py --image-tag my-app:1.0.0
  ```

- **Deploy to PyPI (Test):**

  ```bash
  pyforge-deploy deploy-pypi --test --bump patch
  ```

- **Deploy a Specific Version:**

  ```bash
  pyforge-deploy deploy-pypi --version 2.1.0
  ```

- **Show Detected Dependencies:**

  ```bash
  pyforge-deploy show-deps
  ```

- **Show Project Version:**

  ```bash
  pyforge-deploy show-version
  ```

---

## Configuration

For PyPI publishing, provide an API token. Create a `.env` file in your project root:

```
PYPI_TOKEN=pypi-your-token-here
```

Or export `PYPI_TOKEN` as an environment variable in your shell or CI/CD system.

---

## How It Works

- **Version Engine:** Resolves project version from `pyproject.toml`, `__about__.py`, or `.version_cache`. Fetches latest PyPI version to avoid conflicts, writes final version to `src/<package_name>/__about__.py`.
- **Docker Builder:** Detects dependencies and Python version, renders `Dockerfile.j2`, and builds the image.
- **PyPI Distributor:** Cleans build artifacts, runs `python -m build`, uploads distributions with `twine`.

---

## Testing

Run the full test suite with:

```bash
pytest
```

Unit tests cover the CLI and builder components, located in the `tests/` directory.


## License

MIT License. See [LICENSE](LICENSE) for details..
