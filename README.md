# pyforge-deploy

pyforge-deploy is an automation tool for building and publishing Python projects. It provides commands to build Docker images, generate project-specific Dockerfiles from templates, and build and upload packages to PyPI or TestPyPI. The tool is intended for use in CI or local development workflows where consistent packaging and deployment are required.

## Features

- Detect project dependencies from `pyproject.toml` and fallback to `pip freeze` when necessary.
- Generate Dockerfiles using Jinja2 templates configured for the detected Python runtime and dependencies.
- Build source and wheel distributions and upload them to PyPI or TestPyPI with token-based authentication.
- Automatic version calculation and bumping (patch, minor, major) using local metadata and remote package data.
- CLI to drive common tasks: Docker build, PyPI deploy, show dependencies, and show version.

## Tech Stack

- Language: Python 3.12+
- Templating: Jinja2
- Packaging: setuptools, build, twine
- Testing: pytest, unittest
- Linters and security: Ruff, Bandit, Mypy, pre-commit
- Container tooling: Docker

## Installation

Clone the repository and install development dependencies in a virtual environment.

```bash
git clone https://github.com/your-org/pyforge-deploy.git
cd pyforge-deploy
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
pip install .
```

Optional: install Docker on the host if you will build images locally.

## Usage

Use the provided CLI entrypoint to run common tasks. Run the help command to list available subcommands and options.

```bash
pyforge-deploy --help
```

Common commands

```bash
# Build a Docker image using project templates and detected dependencies
pyforge-deploy docker-build --entry-point src/main.py --image-tag my-app:v1

# Build and publish to TestPyPI or PyPI
pyforge-deploy deploy-pypi --test --bump patch

# Show resolved dependencies
pyforge-deploy show-deps

# Show computed project version
pyforge-deploy show-version
```

Configuration

- Place environment variables in a `.env` file or export them in CI. The primary variable used for publishing is `PYPI_TOKEN`.

## Testing

Run the test suite with pytest.

```bash
pytest
```

The repository includes unit tests covering the CLI and builder components under the `tests` directory.

## Development notes

- Source code lives in `src/pyforge_deploy` and follows a small builder pattern for Docker and PyPI actions.
- The version engine provides deterministic version bumping and caches results to avoid repeated network calls.

## License

This project is released under the MIT License. See the `LICENSE` file for details.

## Contributing

Contributions are welcome. For significant changes, open an issue to discuss the intended work, and submit pull requests with tests and documentation updates.

