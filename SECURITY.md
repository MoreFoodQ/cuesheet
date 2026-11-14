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

Each process is stopped once it has answered. The CLIs contact their own services with their own sign-in. CueSheet makes no network requests of its own.

## What CueSheet reads

- The output of the commands above.
- `~/.codex/sessions/**/*.jsonl`, only when the live Codex query fails. CueSheet reads the last `rate_limits` entry and ignores the rest of the file.
- `agents.json` and, if present, `config.json` in the repository.

CueSheet does not read credential files, OAuth tokens, API keys, keychains, or browser data.

## What CueSheet writes

- `data/latest.json` in the repository. It contains quota percentages, reset times, plan names, and the number of Codex reset credits. Git ignores it.
- `data/models_seen.json` in the repository: model ids and the date each was first seen. Git ignores it.
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
