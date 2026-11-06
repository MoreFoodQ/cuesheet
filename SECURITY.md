# Security

CueSheet only runs the CLIs you already have installed and reads what they report. This page lists everything it touches.

## What CueSheet runs

| Command | Purpose |
|---|---|
| `claude -p --input-format stream-json --output-format stream-json --verbose --no-session-persistence --strict-mcp-config` | Sends one `get_usage` control request. No prompt is sent. |
| `codex app-server` | Sends `initialize` and `account/rateLimits/read`. No other method is called. |
| `agy -p "/usage" --output-format json` | Runs the read-only `/usage` command. |

Each process is stopped once it has answered. The CLIs contact their own services with their own sign-in. CueSheet makes no network requests of its own.

## What CueSheet reads

- The output of the commands above.
- `~/.codex/sessions/**/*.jsonl`, only when the live Codex query fails. CueSheet reads the last `rate_limits` entry and ignores the rest of the file.
- `agents.json` and, if present, `config.json` in the repository.

CueSheet does not read credential files, OAuth tokens, API keys, keychains, or browser data.

## What CueSheet writes

- `data/latest.json` in the repository. It contains quota percentages, reset times, plan names, and the number of Codex reset credits. Git ignores it.
- `~/.claude/skills/cuesheet/SKILL.md`, only when you run `install-skill.ps1`.

## Reporting a problem

Open an issue on GitHub. Do not paste tokens, API keys, or the contents of your credential files into the issue.
