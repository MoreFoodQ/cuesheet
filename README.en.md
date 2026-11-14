<p align="center">
  <img src="assets/banner.png" alt="CueSheet" width="820">
</p>

<p align="center">
  <a href="https://github.com/MoreFoodQ/cuesheet/actions/workflows/test.yml"><img src="https://github.com/MoreFoodQ/cuesheet/actions/workflows/test.yml/badge.svg" alt="test"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license">
</p>

<p align="center"><a href="README.md">繁體中文</a> | English</p>

CueSheet is a command-line tool that reports the remaining usage quota of three AI coding agents on one machine: Claude Code, Codex CLI, and the Antigravity CLI (`agy`). It prints every quota window with its reset time and writes the same data as JSON, so scripts and other agents can check quota before they hand out work. Scheduled tasks can go through `cue relay`, which picks an agent that still has quota and hands the task to the next agent if the first one runs out partway.

## What it reports

| Agent | Quota windows |
|---|---|
| Claude Code | 5-hour and weekly, plus per-model weekly limits when the plan has them |
| Codex CLI | 5-hour and weekly for the `codex` limit, and the number of free reset credits |
| Antigravity `agy` | 5-hour and weekly for the Gemini group and for the Claude/GPT group, and the separate weekly Gemini 4 Argon quota on Pro plans |

Each agent in `agents.json` maps to one or more of these windows. CueSheet reports the lowest remaining value among them and names the window that sets it. Gemini 4 Argon, for example, is limited both by the Gemini group and by its own weekly quota.

## Requirements

- Windows 10 or later. The tests also run on Linux, but the CLI integration has only been tested on Windows.
- Python 3.10 or later. There are no third-party dependencies.
- The CLIs you want to check, installed and signed in: `claude`, `codex`, `agy`. A missing CLI is reported as unavailable and does not affect the other two.

## Installation

```powershell
git clone https://github.com/MoreFoodQ/cuesheet.git
cd cuesheet
```

## Usage

```powershell
.\cue.ps1                  # query all agents, print a table, write data\latest.json
.\cue.ps1 --json           # print JSON instead of the table
.\cue.ps1 --only codex     # query some agents only: claude, codex, gemini
.\cue.ps1 --max-age 600    # reuse data\latest.json if it is less than 600 seconds old
.\cue.ps1 --agent luna     # report one agent and set the exit code
```

`python cue.py` accepts the same options. A full query takes 10 to 20 seconds because the three CLIs run in parallel.

Example output (the table is in Traditional Chinese):

```
■ 派工參考（剩餘＝該代理相關額度裡最少的那一個）
  狀態    代理                      剩餘  卡在                          重置
  [正常]  Claude Code（主控）       61%   每週（全部模型）              11/11 04:00（4 天 6 小時後）
  [偏低]  Luna（Codex gpt-6-luna）  11%   codex 每週                    11/09 23:18（3 天 1 小時後）
  [正常]  Gemini 4 Argon（agy）     28%   Argon 專屬 每週               11/09 22:43（3 天 1 小時後）
  [正常]  Gemini 3.8 Flash（agy）   95%   Gemini 組（Flash＋Pro） 每週  11/09 21:25（2 天 23 小時後）
  [用完]  agy 內的 Claude／GPT-OSS  0%    Claude／GPT 組 每週           11/09 22:43（3 天 1 小時後）
```

### Exit codes

With `--agent`, the exit code gives the agent's level: `0` ok, `1` low (below 20%), `2` exhausted (1% or less), `3` unknown. The thresholds are set in `agents.json`.

```powershell
.\cue.ps1 --agent luna --max-age 600
if ($LASTEXITCODE -ge 2) { "Luna is out of quota" }
```

## JSON output

Each query writes `data/latest.json`, which Git ignores. Scripts that only need a dispatch decision can read the `agents` array:

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-11-06T13:26:00+00:00",
  "agents": [
    {
      "id": "luna",
      "remaining_percent": 11.0,
      "limiting_bucket": "codex.weekly",
      "resets_at": "2026-11-09T15:18:37+00:00",
      "level": "low",
      "data_status": "ok",
      "notes": []
    }
  ],
  "providers": { "claude": {}, "codex": {}, "gemini": {} }
}
```

All times are UTC. `level` is one of `ok`, `low`, `exhausted`, `unknown`. `data_status` is `ok` for a live reading, `stale` for an old snapshot, and `unavailable` when nothing could be read. A `remaining_percent` of `null` means the value could not be read; the reason is in `notes` or in `providers.<name>.error`. `providers` holds every quota window for each agent.

## Claude Code skill

`skill/cuesheet/SKILL.md` tells Claude Code to check quota before it delegates work to Codex or `agy`, and gives a table for choosing an agent by task type. Install it with:

```powershell
.\install-skill.ps1
```

The script copies the skill to `~/.claude/skills/cuesheet/` and fills in the path to this repository.

## Scheduled relay (cue relay)

`cue relay` decides which agent runs a scheduled task based on quota. Tasks live in `relay.json`; each has a prompt, a working folder, and an agent order. At run time CueSheet checks quota, skips agents that are out, and gives the task to the first agent in the order that has quota. If that agent runs out partway, the next agent runs the task again. `cue relay` is not an AI and uses no quota itself.

```powershell
copy relay.example.json relay.json        # first time: copy the example and edit it
.\cue.ps1 relay --list                    # list tasks
.\cue.ps1 relay daily-notes --dry-run     # show who would run it, without running
.\cue.ps1 relay daily-notes               # run it
.\schedule-relay.ps1 -Task daily-notes -At 09:00   # add a daily 09:00 job to Windows Task Scheduler
```

A task looks like this:

```jsonc
"daily-notes": {
  "workdir": "C:\\path\\to\\project",
  "prompt": "Summarize the .md files in notes changed today into summaries/YYYY-MM-DD.md.",
  "order": ["claude", "luna", "flash"],   // who goes first
  "min_remaining_percent": 20,            // agents below this move to the back
  "timeout_minutes": 30,
  "handover_on": "quota",                 // quota: hand over only when quota runs out; any_failure: on any failure
  "success_file": "summaries/latest.md"   // optional: the run counts as a success only if this file was written
}
```

Agents with at least `min_remaining_percent` left go first, in order. Agents that are low or whose quota could not be read go after them, and exhausted agents are skipped. A handover happens when the agent's output contains a quota-exhausted message, or when a failed run is followed by a quota check that shows the agent is out.

Exit codes: `0` success, `1` the task failed for a reason other than quota, `2` no agent had quota, `3` configuration error or the same task is already running. Every run is recorded in `data/relay/history.jsonl`, and each agent's full output is saved in `data/relay/logs/`.

Built-in agents:

| Agent | Command | What it can do unattended |
|---|---|---|
| `claude` | `claude -p --permission-mode acceptEdits` | Read and write files in the working folder; shell commands that need approval are denied |
| `luna` | `codex exec -s workspace-write -m gpt-6-luna -C <workdir>` | Run commands in a sandbox that can only write the working folder |
| `flash` | `agy -p ... --model gemini-3.8-flash-medium --mode accept-edits` | Read and write files; shell commands are denied |
| `argon` | `agy -p ... --model gemini-4-argon-medium --effort medium --mode accept-edits` | Same as `flash` |

The `runners` section of `relay.json` can change these arguments or add agents. `quota_agent` sets which agent's quota a runner follows.

Things to keep in mind when writing tasks:

- The agent that takes over does not know how far the previous one got and starts again. Tasks that write their result to a file are safe to run twice.
- Nobody is there to approve tools, so tools that need approval are denied. `agy` still exits with code 0 when this happens; `cue relay` recognizes the message and records a permission problem. Tasks that only read and write files are the most reliable.
- Flash costs less than Argon and Luna and is weaker at reasoning. Leave it out of the order for hard tasks.
- Scheduled jobs run only while you are signed in to Windows, because each CLI keeps its sign-in under your user account.

## How quota is read

Each agent is queried through a read-only command of its own CLI. These commands do not start a model turn and do not use quota. CueSheet does not read, store, or send credentials; each CLI handles its own sign-in. [SECURITY.md](SECURITY.md) lists everything CueSheet reads and writes.

| Agent | Command | Limitations |
|---|---|---|
| Claude Code | `claude -p --input-format stream-json` with a `get_usage` control request | The control protocol is internal and undocumented, so a Claude Code update can change it. The query runs with `--no-session-persistence` and leaves no session file. |
| Codex CLI | `codex app-server`, method `account/rateLimits/read` | The app server is marked experimental. If the call fails, CueSheet reads the last `rate_limits` entry in `~/.codex/sessions` and marks the result `stale`. A window whose reset time has passed since that snapshot is reported as unknown. |
| Antigravity `agy` | `agy -p "/usage" --output-format json` | Reports remaining fractions only. Older `agy` versions send `/usage` to the model as a prompt; CueSheet detects this from `num_turns` and reports the agent as unavailable. |

In Git Bash, running `agy -p "/usage"` by hand sends a prompt to the model. MSYS path conversion rewrites `/usage` to `C:/Program Files/Git/usage` before `agy` sees it. Run the command from PowerShell instead. CueSheet passes arguments directly and is not affected.

The Codex app server also has a method, `account/rateLimitResetCredit/consume`, that spends a reset credit. CueSheet never calls it.

In local Codex session logs, every model, including `gpt-6-luna`, reports against the `codex` limit. OpenAI does not document this.

Last tested with Claude Code 2.1.304, codex-cli 0.164.0, and `agy` on 2026-11-06.

## Configuration

`agents.json` maps agents to quota windows and sets the low and exhausted thresholds. The included agents are `claude`, `luna`, `argon`, `flash`, and `agy-3p`. Windows listed under `buckets` must exist in the provider's output; windows under `optional_buckets` count only when present.

If a CLI is not on `PATH`, copy `config.example.json` to `config.json` and set its location. Git ignores `config.json`.

## Development

```powershell
python -m unittest discover -s tests
```

The tests parse recorded CLI responses in `tests/fixtures`, and the relay tests simulate exhausted quota with fake agents. No AI CLI is called. Each agent has its own module in `cuesheet/` (`claude.py`, `codex.py`, `gemini.py`), so a change in one CLI's output usually needs a fix in one file.

## Related projects

[CodexBar](https://github.com/steipete/CodexBar) is a macOS menu bar app covering many providers. [caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) is a cross-platform Rust CLI. [cclimits](https://github.com/cruzanstx/cclimits) is a CLI for macOS and Linux. [Token Monitor](https://github.com/Javis603/token-monitor) is a desktop widget. Several of these read OAuth tokens or browser cookies and call provider APIs directly. CueSheet runs each CLI's own read-only command and reports which quota window limits each agent.

## License

[MIT](LICENSE)
