# Changelog

## [Unreleased]

### Changed
- Tightened the GitHub Actions workflows to install the project in editable mode for CI runs.
- Narrowed the release tag filter so the release workflow only runs for version-like tags.
- Updated CI checks to target the installed package for coverage and type checking.
- Migrated version bump semantics to Pride versioning with `proud/default/shame` as primary bump names.
- Updated CLI help, defaults, and status guidance to use Pride bump naming while preserving existing pre-release bump options.
- Updated release workflow dispatch inputs to expose Pride bump choices first.
- Improved the Dockerfile template with cleaner runtime stage logic and consistent pip execution via `python -m pip`.

### Fixed
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

### Performance
- Reduced CI setup drift by reusing the same editable install across lint, test, type-check, and audit jobs.
- Reduced Docker image size and layer churn by removing duplicated local-site package copies and tightening runtime cleanup.
