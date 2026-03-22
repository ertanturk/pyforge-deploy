# Changelog

## v1.5.0
- Added
  - Introduce focused `pyforge release` flow

## [v1.4.1] - 2026-03-21

### Features
- Auto-detect OpenRouter keys & set headers (bc8ee92)
  Detect OpenRouter-style OPENAI_API_KEY values (sk-or-v1-*) and default the AI base URL to https://openrouter.ai/api/v1 unless a base URL is explicitly configured. Rework OPENAI/PYFORGE AI base/key resolution logic, add OpenRouter-specific request headers (X-Title and HTTP-Referer) from env vars when routing to openrouter.ai.

### Bug Fixes
- Fix provider failure logging to report the actual provider name instead of a hardcoded "Gemini" (bc8ee92).

### Maintenance
- Updated docs and added tests covering OpenRouter key autodetection and header injection (bc8ee92).
## [v1.4.0] - 2026-03-21
### Features
* Support explicit AI provider & shared API key (e299423)

## [v1.3.2] - 2026-03-21
### Other Changes
* Better env/setting parsing and PyPI validation (eec6342)

## [v1.3.1] - 2026-03-21
### Bug Fixes
* Update changelogs for v1.2.9 and fix whitespace (b0e1a0a)
### Chores
* Changelog: range-aware bump and section merge (c8d3961)
### Other Changes
* Respect merged git tags when resolving versions (dd91ac3)
