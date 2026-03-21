# Changelogs

### Changed
- Reorganized generated GitHub Actions release workflow into categorized subprocess jobs (`quality_and_security`, `deploy_pypi`, `deploy_docker`) instead of a single monolithic deployment step.
- Improved Docker dependency wheelhouse generation to prefer `uv pip wheel` when available, reducing dependency resolution time in modern environments.
- Redesigned CLI help menus with a clearer command center layout, richer quick-start guidance, and grouped subcommand option sections for a more engaging terminal experience.
- Extended the shared logging system to emit richer structured payloads (timestamp, event type, component, CI metadata) when JSON logs are enabled.
- Standardized internal module debug logging to use the central logger for consistent output across CLI, version, Docker, and parallel execution paths.
- Tightened the GitHub Actions workflows to install the project in editable mode for CI runs.
- Narrowed the release tag filter so the release workflow only runs for version-like tags.
- Updated CI checks to target the installed package for coverage and type checking.
- Migrated version bump semantics to Pride versioning with `proud/default/shame` as primary bump names.
- Updated CLI help, defaults, and status guidance to use Pride bump naming while preserving existing pre-release bump options.
- Updated release workflow dispatch inputs to expose Pride bump choices first.
- Improved the Dockerfile template with cleaner runtime stage logic and consistent pip execution via `python -m pip`.
- Categorized composite CI steps into Quality/Security/Deploy phases for clearer pipeline visibility.

### Fixed
- Fixed persistent runtime `ModuleNotFoundError` issues (e.g., `packaging`) by adding a final-image dependency sync step from `requirements-docker.txt` during Docker runtime stage assembly.
- Fixed intermittent Docker runtime import errors by reapplying `requirements-docker.txt` at project-install step, preventing missing dependencies from cached build layers.
- Fixed wheelhouse-mode Docker builds failing on `setuptools>=68` by installing setuptools in the builder stage and disabling build isolation for local project install.
- Fixed Docker runtime dependency errors by installing package dependencies during project install in the image build stage (instead of forcing `--no-deps`).
- Fixed Docker runtime startup failure for src-layout projects by normalizing detected entry points to container-valid paths (e.g., `pyforge_deploy/cli.py` → `src/pyforge_deploy/cli.py`).
- Fixed Docker wheelhouse acceleration to probe `uv pip wheel` capability first, avoiding unsupported-subcommand failures on older uv versions and keeping CI logs clean.
- Fixed Docker wheelhouse generation resilience by automatically falling back to `pip wheel` when `uv` is unavailable or fails, keeping CI builds reliable.
- Fixed dependency auditing to scan the resolved CI environment instead of a static requirements file.
- Fixed the composite release action to run tests against the locally installed project instead of an invalid `uvx` invocation.
- Fixed security scanning to skip test and virtual environment paths during Bandit analysis.
- Fixed the release workflow template so generated workflows only trigger on version-like tags.
- Fixed the release workflow bump selector to use a valid GitHub Actions choice input.
- Fixed configuration resolution so environment variables still apply when `pyproject.toml` parsing fails.
- Fixed bump validation in the composite action to accept Pride bump names (`proud/default/shame`) in addition to legacy aliases.
- Fixed dynamic version resolution to auto-normalize legacy numeric versions into Pride-compatible core ordering while retaining PEP 440 suffixes.
- Fixed non-root Docker image builds to avoid duplicated `/root/.local` copies in the final image stage.
- Fixed CI preflight behavior to fail fast when Docker CLI/daemon is unavailable for Docker-enabled runs.
- Fixed Docker yes/no prompt handling so `auto_confirm` from config/env works when `--yes` is not explicitly passed.
- Fixed Docker multi-platform publishing reliability by correctly parsing boolean env flags (e.g., `PYFORGE_DOCKER_WHEELHOUSE=false`) in Docker builder logic.
- Fixed Docker multi-platform/ARM builds to auto-disable local wheelhouse usage and avoid architecture-specific wheel resolution failures during offline installs.
- Fixed Docker tag publishing robustness by normalizing repository/user image coordinates to lowercase in release tag builds.

### Performance
- Improved CI troubleshooting speed by attaching provider/run metadata directly to JSON log events.
- Reduced CI setup drift by reusing the same editable install across lint, test, type-check, and audit jobs.
- Reduced Docker image size and layer churn by removing duplicated local-site package copies and tightening runtime cleanup.
- Reduced CI setup time by preferring a single editable install with dev extras (`-e .[dev]`) during test-enabled runs.
