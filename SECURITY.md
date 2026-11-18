# Security

CueSheet only runs the CLIs you already have installed and reads what they report. This page lists everything it touches.

## What CueSheet runs

| Command | Purpose |
|---|---|
| `claude -p --input-format stream-json --output-format stream-json --verbose --no-session-persistence --strict-mcp-config` | Sends one `get_usage` control request. No prompt is sent. |
| `codex app-server` | Sends `initialize` and `account/rateLimits/read`. No other method is called. |
| `agy -p "/usage" --output-format json` | Runs the read-only `/usage` command. |

To list models (`cue models`, and in the background of each `cue` run), CueSheet also runs:

| Command | Purpose |
|---|---|
| `claude -p --input-format stream-json ... --no-session-persistence --strict-mcp-config` | Sends one `initialize` control request and reads the model list. No prompt is sent. |
| `codex app-server` | Sends `initialize` and `model/list`. |
| `agy models` | Lists available models. |

To check CLI versions, CueSheet runs `claude --version`, `codex --version`, and `agy --version`, and reads agy's own update log in `~/.gemini/antigravity-cli/updater/update_status.json`. It never runs an update command.

Each process is stopped once it has answered. The CLIs contact their own services with their own sign-in.

To check CLI versions, CueSheet reads public version numbers from the npm registry (`registry.npmjs.org/-/package/@openai/codex/dist-tags` and `@anthropic-ai/claude-code`). No account data is sent.

`cue rank` and `cue boards` also download public leaderboard data, at most once a day: the Hugging Face datasets API for `lmarena-ai/leaderboard-dataset`, `epoch.ai/data/benchmark_data.zip`, OpenRouter's daily export (`openrouter.ai/api/v1/datasets/exports/`), and `models.dev/api.json`. These requests carry no account data. The files are stored in `data/boards/`, which Git ignores.

If you add an Artificial Analysis key, CueSheet also calls `artificialanalysis.ai/api/v2/language/models/free` with the key in the `x-api-key` header, at most once a day unless you pass `--refresh`. It never requests AA web pages. The key is read from the `CUESHEET_AA_KEY` environment variable or `aa_api_key` in `config.json`; it is never written to `data/`, logs, or output.

## What CueSheet reads

- The output of the commands above.
- `~/.codex/sessions/**/*.jsonl`, only when the live Codex query fails. CueSheet reads the last `rate_limits` entry and ignores the rest of the file.
- `agents.json`, `runners.json`, `model_map.json`, `tiers.json`, and, if present, `config.json`, `model_map.local.json`, and `tiers.local.json` in the repository.

CueSheet does not read credential files, OAuth tokens, keychains, or browser data. The only key it reads is the optional Artificial Analysis key you put in `config.json` or `CUESHEET_AA_KEY`.

## What CueSheet writes

- `data/latest.json` in the repository. It contains quota percentages, reset times, plan names, and the number of Codex reset credits. Git ignores it.
- `data/models_seen.json` in the repository: model ids and the date each was first seen. Git ignores it.
- `data/relay/usage_samples.jsonl`: quota percentages before and after each relay attempt. Git ignores it. To flag samples taken while you were using the same CLI, CueSheet counts how many session files under `~/.claude/projects`, `~/.codex/sessions`, and `~/.gemini/antigravity-cli/conversations` were modified during the run. It reads only file modification times, never file contents.
- `runners.json`, only when you run `cue models --update`.
- `~/.claude/skills/cuesheet/SKILL.md`, only when you run `install-skill.ps1`.

## cue relay

`cue relay` is different from the quota check: it runs the agents for real. Each run uses that agent's quota and can change files in the task's `workdir`.

- The built-in runners keep each agent inside the working folder: Claude Code with `--permission-mode acceptEdits`, Codex with the `workspace-write` sandbox and `-C <workdir>`, and `agy` with `--mode accept-edits`. Tools that need approval are denied because nobody is there to approve them.
- CueSheet never adds `--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox`, or similar flags. If you add one in `relay.json`, the agent can run any command on your machine.
- `relay.json` is ignored by Git because it holds your prompts and folder paths.
- `data/relay/logs/` stores each agent's full output. Treat it like the files in your working folder.
- `schedule-relay.ps1` registers a Windows scheduled task under your account. Remove it with `-Remove`.

## Reporting a problem

Open an issue on GitHub. Do not paste tokens, API keys, or the contents of your credential files into the issue.
