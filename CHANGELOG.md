# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-11-14

### Added

- `cue models`: lists the models each platform offers right now (Claude Code `initialize`, Codex `model/list`, `agy models`) and checks every relay model in `runners.json` for missing models, newer versions in the same family, and unsupported reasoning effort. `--update` writes newer same-family versions back to `runners.json`.
- Each `cue` run fetches the model lists in parallel and lists, under "需要注意", quota windows no agent follows, models first seen in the last 7 days, and relay model problems.
- `cue relay` recognizes "model not found" errors from all three CLIs and hands the task to the next agent.

### Changed

- Relay models moved from code to `runners.json` (platform, model, effort, family). `relay.json` can override single fields.
- Gemini group names are built from the model list in the `agy` response instead of a fixed label.

### Fixed

- Gemini quota windows that have not started yet are shown as reported, with a note.

## [0.3.0] - 2026-11-14

### Changed

- The default relay order is now Claude Opus 5.5 (medium), Sol `gpt-6.1-sol` (medium), and Gemini 4 Argon (medium). These three draw on separate quotas and are of the same tier. A task without `order` uses this default, and `default_order` in `relay.json` overrides it.
- Flash is no longer used as a fallback for other agents, in the relay or in the Claude Code skill.

### Added

- `sol` agent for Codex `gpt-6.1-sol` in `agents.json` and in the relay runners.

## [0.2.0] - 2026-11-14

### Added

- `cue relay`: runs a task from `relay.json` with the first agent that has quota, and hands it to the next agent when quota runs out partway.
- Quota exhaustion is detected from each CLI's error message, or by checking quota again after a failed run.
- Runs blocked by unattended permission denials are recorded as permission problems instead of successes.
- `schedule-relay.ps1` to add, list, and remove daily Windows scheduled tasks.
- Run history in `data/relay/history.jsonl` and per-agent logs in `data/relay/logs/`.

## [0.1.0] - 2026-11-06

### Added

- Quota readers for Claude Code (`get_usage` control request), Codex CLI (`account/rateLimits/read`), and Antigravity `agy` (`/usage`).
- Fallback to the last Codex session snapshot when the live query fails.
- Per-agent summary with the limiting quota window, configured in `agents.json`.
- Table output, JSON output to `data/latest.json`, `--max-age` caching, and `--agent` exit codes.
- Claude Code skill and `install-skill.ps1`.
- Unit tests on recorded CLI responses and a GitHub Actions workflow for Windows and Linux.

[0.4.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.4.0
[0.3.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.3.0
[0.2.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.2.0
[0.1.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.1.0
