# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-06

### Added

- Quota readers for Claude Code (`get_usage` control request), Codex CLI (`account/rateLimits/read`), and Antigravity `agy` (`/usage`).
- Fallback to the last Codex session snapshot when the live query fails.
- Per-agent summary with the limiting quota window, configured in `agents.json`.
- Table output, JSON output to `data/latest.json`, `--max-age` caching, and `--agent` exit codes.
- Claude Code skill and `install-skill.ps1`.
- Unit tests on recorded CLI responses and a GitHub Actions workflow for Windows and Linux.

[0.1.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.1.0
