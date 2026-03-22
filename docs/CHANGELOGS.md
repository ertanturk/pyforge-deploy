# Changelogs

## [Unreleased]

### Added
- Added a dedicated release orchestration layer at [src/pyforge_deploy/release/service.py](src/pyforge_deploy/release/service.py) to run the full `pyforge release` pipeline with strong defaults.
- Added focused release components for commit analysis, version suggestion, changelog building, and publishing under [src/pyforge_deploy/release](src/pyforge_deploy/release).
- Added a new CLI script alias `pyforge` in [pyproject.toml](pyproject.toml) so the primary command is now `pyforge release`.
- Added a 7-layer heuristic bump engine in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) with score-matrix aggregation, AST-based structural diff signals, dependency-shift scoring, and confidence-gated MAJOR/MINOR/PATCH decisions.

### Changed
- Changed the `release` command UX to interactive-first: analyze commits, show suggested version, preview changelog, then request confirmation before applying.
- Changed release command internals to use Conventional Commit parsing first, heuristic parsing second, and AI fallback only for malformed commits.
- Changed top-level docs to emphasize one-command usage in [README.md](README.md), with `pyforge release` and a before/after workflow.
- Changed non-release commands to be backward-compatible but explicitly deprecated in CLI output, steering users to `pyforge release`.
- Changed release planning in [src/pyforge_deploy/release/service.py](src/pyforge_deploy/release/service.py) to enrich commit metadata (timestamps, parent hashes, file lists, unified diffs) and drive version suggestion from the global confidence-validated bump decision.

### Performance
- Reduced release pipeline complexity by consolidating release decision flow into a single service and removing cross-command publish branching from primary UX.
- Improved squashed-commit handling by shifting impact emphasis to file-density and structural-diff analysis when time-delta signals are unavailable.

### Fixed
- Fixed CI environment detection in color utilities to accept common truthy values like `1` and `yes`, preventing silent misclassification of CI runs.
- Fixed PyPI deploy flag parsing so string values such as `"false"` no longer evaluate as enabled for `pypi_reuse_dist` and `pypi_skip_preflight`.
- Fixed PyPI retry/backoff configuration parsing to safely coerce invalid values to sane defaults instead of crashing upload flow with `ValueError`.
- Fixed PyPI version fetch parsing to reject malformed API payloads missing a valid `info.version`, preventing invalid cache contamination and downstream version errors.
- Fixed release finalization idempotency in [src/pyforge_deploy/release/publisher.py](src/pyforge_deploy/release/publisher.py) by skipping `git commit` when no staged changes exist, preventing empty-commit pipeline crashes.
- Fixed heuristic diff scoring in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) to ignore Python triple-quoted docstring blocks so documentation-only edits do not falsely inflate semantic bump severity.
- Fixed pre-release version parsing in [src/pyforge_deploy/release/version_resolver.py](src/pyforge_deploy/release/version_resolver.py) so tags like `v1.2.3-rc1` no longer reset version progression to `0.0.x`.
- Fixed changelog write idempotency in [src/pyforge_deploy/release/changelog_builder.py](src/pyforge_deploy/release/changelog_builder.py) by skipping duplicate insertion when the target release header already exists.
- Fixed blast-radius dilution in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by switching from average file-weight scoring to max critical-path weighting.
- Fixed release CI trigger gaps in [src/pyforge_deploy/release/publisher.py](src/pyforge_deploy/release/publisher.py) by pushing both the release commit branch and `v*` tag refs after local tagging.
- Fixed first-release history explosion in [src/pyforge_deploy/release/service.py](src/pyforge_deploy/release/service.py) by capping no-tag commit scans to the latest 50 commits.
- Fixed release changelog duplication in [src/pyforge_deploy/release/changelog_builder.py](src/pyforge_deploy/release/changelog_builder.py) by de-duplicating repeated commit summaries while preserving insertion order.
- Fixed false major-version spikes in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by treating common cleanup `remove ...` phrases as non-breaking maintenance.
- Fixed test-heavy scoring inflation in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by applying a test-impact ratio dampener when test files dominate changed paths.
- Fixed under-classification of deprecations in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by adding explicit deprecation signature detection with MINOR-score boosts.
- Fixed revert over-scoring in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by short-circuiting revert commits to patch-maintenance scoring.
- Fixed release diff collection in [src/pyforge_deploy/release/service.py](src/pyforge_deploy/release/service.py) by removing a lowercase diff-filter that silently excluded newly added files from scoring input.
- Fixed AST structural scoring in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) to treat missing old/new git blobs as empty sources so added or deleted Python files still contribute signal.
- Fixed tag-collision release behavior in [src/pyforge_deploy/release/publisher.py](src/pyforge_deploy/release/publisher.py) by aborting before staging/committing when the target release tag already exists.
- Fixed low-confidence AI bump resolution in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) to call the configured `ai_fallback` function instead of only reusing local heuristic parsing.
- Fixed historical change-density determinism in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by reading file line totals from the analyzed commit revision rather than the live working tree.
- Fixed release commit analysis resilience in [src/pyforge_deploy/release/service.py](src/pyforge_deploy/release/service.py) by handling git timeout/subprocess failures in per-commit file-list and diff collection paths.
- Fixed changelog/bump noise from automated release commits in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by filtering `chore(release): ...` commits before scoring.
- Fixed pre-1.0 semantic bump behavior in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) so major-risk signals in `0.x.y` projects are scaled to minor bumps per SemVer initial-development guidance.
- Added schema-migration risk intelligence in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) to detect migration-file changes and destructive database operations (`drop table/column`) as high-impact release signals.
- Added security hotfix override scoring in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) so CVE/GHSA/zero-day/hotfix commits force dominant patch confidence and avoid low-confidence prompt blocking.
- Fixed AST structural-analysis blind spots in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by walking the full syntax tree so class methods and nested functions are included in symbol-diff scoring.
- Fixed release decision threshold brittleness in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by switching to dominant-signal dynamic gates for major/minor classification.
- Fixed first-release version suggestion behavior in [src/pyforge_deploy/release/version_resolver.py](src/pyforge_deploy/release/version_resolver.py) so initial major bumps now resolve to `1.0.0` instead of always forcing `0.1.0`.
- Fixed local-publish release ordering in [src/pyforge_deploy/release/publisher.py](src/pyforge_deploy/release/publisher.py) so remote tag push occurs only after local PyPI publish succeeds.
- Fixed changelog idempotency in [src/pyforge_deploy/release/changelog_builder.py](src/pyforge_deploy/release/changelog_builder.py) by detecting existing release versions across flexible header formats (including dated headers).
- Fixed CI pseudo-TTY prompt hangs in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by skipping interactive bump override whenever `CI=true` or `GITHUB_ACTIONS=true`, even if `isatty()` reports true.
- Fixed git blob read crash risk in [src/pyforge_deploy/release/commit_analyzer.py](src/pyforge_deploy/release/commit_analyzer.py) by handling `TimeoutExpired`/`SubprocessError` and returning `None` for resilient AST/density fallbacks.

### Changed
- Changed changelog AI routing to support explicit provider override via `PYFORGE_AI_PROVIDER`, so users are no longer forced into static key-priority selection.
- Changed changelog AI auth to support shared key usage via `PYFORGE_AI_API_KEY` across providers.
- Changed OpenAI-compatible local endpoint handling to allow no-key local runs when `OPENAI_BASE_URL`/`PYFORGE_AI_BASE_URL` points to localhost.
- Changed OpenAI key routing to auto-detect OpenRouter-style keys (`sk-or-v1-*`) and default to `https://openrouter.ai/api/v1` when no base URL override is provided.

### Fixed
- Fixed provider failure logging text to report the actual selected AI provider instead of always saying "Gemini".

## [v1.2.9] - 2026-03-21


### Added
- Added GitHub Actions `publish_release` job to publish GitHub Releases on tag pushes using version-matched `CHANGELOG.md` sections as release descriptions.
- Added release dirty-tree override controls via `pyforge-deploy release --allow-dirty` and `PYFORGE_RELEASE_ALLOW_DIRTY=1` for intentional non-clean working tree automation.
- Added a multi-provider AI router for changelog generation with provider preference order (`OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → `GEMINI_API_KEY`) and OpenAI-compatible `OPENAI_BASE_URL` support for local LLM endpoints.
- Added AI context-window protection by chunking large malformed-commit inputs and merging per-chunk markdown outputs.
- Added configurable changelog AI prompt override via `[tool.pyforge-deploy.changelog] custom_prompt` in `pyproject.toml`.
- Added a 3-tier hybrid changelog intelligence waterfall in `changelog_engine` with optional Gemini BYOK generation (Tier 1), strict Conventional Commit parsing (Tier 2), and fuzzy keyword fallback categorization for malformed commit messages (Tier 3).
- Added a deterministic `changelog_engine` module that discovers release base tags, parses Conventional Commits with a strict regex scaffold, computes semantic version bump recommendations, and generates structured markdown release notes.
- Added a new CLI command `release` (with backward-compatible `release-intel` alias) to run automated changelog generation with optional `--dry-run` and explicit `--version` targeting.

### Changed
- Changed changelog release-boundary discovery to a smarter strategy chain: top `CHANGELOG.md` version tag (if reachable) → latest reachable semantic tag → `git describe` fallback → latest `chore(release)` commit hash → repository first commit.
- Changed commit extraction to ignore release/merge noise commits by default so release notes focus on user-facing changes.
- Changed `pyforge-deploy release` default behavior to CI-managed publishing: it now finalizes changelog/tag push and lets the tag-triggered workflow publish PyPI, Docker, and GitHub release assets, preventing duplicate publish runs.
- Added explicit `--local-publish` (and `PYFORGE_RELEASE_LOCAL_PUBLISH=1`) opt-in for users who intentionally want local publishing in the same CLI run.
- Changed `pyforge-deploy release --local-publish` to run a complete local release lifecycle: changelog generation, PyPI publish, Docker build/push, git tag finalization, and GitHub Release publication from changelog text.
- Changed AI cost behavior to pre-filter strict Conventional Commits and only send malformed commit messages to AI normalization.
- Changed commit parsing implementation to parallel processing for faster release planning on large commit histories.
- Extended `deploy-pypi` with optional `--release` integration (keeping `--release-intel` compatibility) to trigger post-publish release intelligence automation when explicitly enabled.

### Fixed
- Fixed dynamic version resolution to include the latest merged semantic git tag as a floor candidate, preventing duplicate re-release of an already tagged version when cache or PyPI metadata lags.
- Fixed release git finalization to push explicit remote branch/tag refs and verify the remote tag exists before reporting success, preventing false-positive "pushed" messages.
- Removed `[skip ci]` from release commit messages so tag-triggered release workflows are not inadvertently suppressed.
- Fixed release tagging to enforce canonical `v{version}` format and prevent accidental double-prefix tags (for example `vv1.2.3`) when explicit versions include `v`.
- Fixed `PyPIDistributor` version resolution to avoid forced auto-increment when no explicit bump type is provided, restoring expected direct-instantiation/retry behavior.
- Fixed dry-run version simulation to still read latest PyPI version (read-only), preventing misleading bump previews from stale local/cache baselines.
- Fixed git bump suggestion scope to analyze commits since latest tag (`<tag>..HEAD`) and avoid leaking old breaking changes from prior releases.
- Fixed static-version behavior so explicit bump requests (`--bump` / auto-increment) are respected instead of returning the raw static `pyproject.toml` version.
- Fixed first-release UX by handling PyPI 404 responses as an informational initial-release condition instead of a generic fetch failure warning.
- Fixed Gemini BYOK mode to validate `GEMINI_API_KEY` format (`AIza...`) before API calls, preventing arbitrary non-Gemini keys from being used.
- Hardened release parsing safety by sanitizing commit text and routing non-conforming commit messages to an `Other Changes` bucket instead of failing release generation.


## [v1.2.6] - 2026-03-19


### Added
- Added an intelligent plugin hook engine with resilient shell-command execution from `[tool.pyforge-deploy.plugins]`, including canonical lifecycle stages (`before_build`, `after_build`, `before_release`, `after_release`) and legacy alias compatibility (`pre_*` / `post_*`).
- Added CLI lifecycle integration so Docker and PyPI deploy commands now run configurable hook stages before and after their core deployment actions.
- Added plugin-specific configuration helpers to parse and normalize hook command lists from `pyproject.toml`.
- Added CI integration knobs for plugin command timeout via `plugin_timeout_seconds` in workflow templates and composite action inputs.

### Fixed
- Fixed deployment resilience by downgrading plugin hook failures, non-zero exits, missing executables, and timeouts to warnings so the primary CI/CD pipeline continues.
- Fixed plugin hook resolution order to be deterministic when both canonical and legacy stage keys are configured, ensuring stable command execution sequencing across CI runs.
- Fixed intermittent GitHub Actions cache restore warnings (`Cache service responded with 400`) by disabling `setup-uv` internal cache in the composite action while retaining project-level caching.

### Performance
- Added configurable per-hook timeout control (`plugin_timeout`, `PYFORGE_PLUGIN_TIMEOUT_SECONDS`) to prevent hung plugin scripts from blocking CI runners.

### Changed
- Migrated project version source of truth from `.version_cache`/`__about__.py` to `.pyforge-deploy-cache/version_cache`, simplifying version management and removing package-file version coupling.
- Changed generated CI/CD workflow and composite action to be plugin-first for quality/security execution, removing built-in hardcoded lint/test/audit stages.
- Reorganized generated GitHub Actions release workflow into deployment-focused subprocess jobs (`deploy_pypi`, `deploy_docker`) with plugin-driven pre/post hook extensibility.
- Moved Docker wheelhouse artifacts from project-root `wheels/` to `.pyforge-deploy-cache/wheels` to keep repositories cleaner while preserving offline dependency reuse.
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
- Fixed CI behavior so deployment can proceed directly when no plugin hooks are configured, while still allowing users to define categorized checks in plugin hook stages.
- Fixed local pre-commit coverage hook execution by running tests via `.venv/bin/python -m pytest`, avoiding executable and dependency lookup failures in environment-specific PATH setups.
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
