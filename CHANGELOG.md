# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.10.1] - 2026-11-20

### Fixed

- A quota or model check could run far past `--timeout`. On Windows, `subprocess.run` waits for the output pipe to close even after a timeout, so a child process that inherits the pipe (agy, for example, starts a background updater) kept the check waiting. Commands now stop at the timeout, the whole process tree is ended, and CueSheet waits at most 5 seconds for leftover output.
- agy quota and model list checks retry once when they fail. agy checks for updates on startup, and the first start after an update can be slow.

### Changed

- When a live quota check fails, `cue` shows the last successful result from `data/latest.json` (up to 24 hours old) marked as an old snapshot, with the reason and the time it was read. Windows whose reset time has passed are shown as unknown.

## [0.10.0] - 2026-11-19

### Changed

- The default relay order is now Claude Opus 5.5 medium → GPT-6.2 Sol medium → Gemini 4 Neon medium. Neon medium (AA 54.9, $1.90 per task) scores higher than Hydrogen high (50.8, $1.87) at about the same cost and draws on the same Gemini group quota. Hydrogen stays available as a runner. Neon is new; so far only AA scores it.

### Added

- `neon` agent in `agents.json` and runner in `runners.json`.

## [0.9.0] - 2026-11-18

### Changed

- The default relay order is now Claude Opus 5.5 medium → GPT-6.2 Sol medium → Gemini 4 Hydrogen high. Hydrogen high replaces Argon medium: it scores higher on AA (50.8 vs 47.6), Arena Agent, and CursorBench, costs less than half per task, and draws only on the Gemini group quota. Argon stays available as a runner.

### Added

- `hydrogen` agent in `agents.json` and runner in `runners.json`.
- Optional runners (`"optional": true`). When your account does not offer the model, `cue models` shows it as not offered, with no warning, and difficulty-based picking leaves it out. `runners.json` includes Gemini 4 Carbon as an optional runner.

## [0.8.0] - 2026-11-18

### Added

- Tasks in `relay.json` can set `difficulty` (`hard`, `normal`, `easy`) or `min_tier` / `max_tier`, and `cue relay` picks agents from `runners.json` by AA tier. `hard` keeps the baseline tier or better, highest score first. `normal` keeps the baseline tier or better and, once every candidate has at least 5 clean samples, prefers the agent with the most runs left; otherwise it follows `default_order`. `easy` sends the weakest model first. Unranked models and platforms that cannot meet `requires_shell` are left out. Tasks without these fields work as before.
- `cue relay --stats [task]`: clean sample counts, average quota used per run, and estimated runs left for each task and model.
- `--dry-run` prints each candidate's tier, score, and samples, and why others were left out. The run history keeps the same lines.

## [0.7.0] - 2026-11-18

### Added

- Artificial Analysis Intelligence Index as a leaderboard, read only through the official free API with your own key (`aa_api_key` in `config.json` or `CUESHEET_AA_KEY`). Each refresh uses 4 of the 100 daily requests. Without a key the source is shown as disabled. Models that leave the list keep their last score and are marked as dropped.
- `cue tiers`: groups each model and effort into L1–L5 by AA index, listing only models in your subscriptions (`--all` for every model). Bands are in `tiers.json`; `tiers.local.json` can override them or assign a tier by hand.
- `cue rank` shows each model's tier on the AA table and the baseline's tier in the header. New `--limit` option.

### Changed

- When a leaderboard has more matches than the limit, `cue rank` keeps models cheaper than the baseline first, then the closest in score, and says how many were left out. Before, it kept the highest scores, which could hide a close, much cheaper model.

## [0.6.0] - 2026-11-18

### Added

- `cue rank <model> [--effort]`: one table per leaderboard with the baseline, higher-scoring models, close models, cost compared with the baseline, and whether each model is configured, available on a platform, or outside your subscriptions. When a leaderboard lacks the requested effort, the nearest effort is shown and labeled. A summary lists models that are as good or better and cheaper.
- `cue boards [--refresh]`: no-key leaderboard and price data from LMArena (Arena Agent), Epoch AI (ECI, CursorBench, SciCode, CritPt), OpenRouter (GPQA Diamond), and models.dev, cached daily in `data/boards/`. A failing source keeps its last data and is marked as old.
- `model_map.json` and optional `model_map.local.json` for name exceptions and names to ignore.
- The relay hands over when a CLI is not logged in, and the Claude quota check says so plainly.

## [0.5.0] - 2026-11-18

### Added

- CLI version check: compares the installed Claude Code and Codex CLI with the npm registry (Claude Code on its configured update channel) and reads agy's own update log. Outdated CLIs are listed under "需要注意" and at the top of `cue models`. Nothing is installed.
- `cue relay` reads the agent's quota before and after each attempt and appends a sample to `data/relay/usage_samples.jsonl`, tagged `clean`, `stale_snapshot`, `incomplete`, `window_reset`, `capped`, or `concurrent`. Samples are only recorded for now.
- `docs/ROADMAP.md`: plan for leaderboard aggregation, tiers, task-difficulty routing, and notifications.

## [0.4.1] - 2026-11-18

### Fixed

- Claude Code 2.1.311 changed the model list in its `initialize` response (display names without versions). CueSheet now reads `resolvedModel` and `supportedEffortLevels`, so Opus 5.5 is no longer reported as missing, and Claude efforts are checked too.

### Changed

- The `sol` relay runner now uses `gpt-6.2-sol`, which Codex CLI 0.170.0 offers. Older Codex CLI versions do not list it; update Codex CLI to get it.

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

[0.6.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.6.0
[0.5.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.5.0
[0.4.1]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.4.1
[0.4.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.4.0
[0.3.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.3.0
[0.2.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.2.0
[0.1.0]: https://github.com/MoreFoodQ/cuesheet/releases/tag/v0.1.0
