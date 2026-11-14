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

All times are UTC. `level` is one of `ok`, `low`, `exhausted`, `unknown`. `data_status` is `ok` for a live reading, `stale` for an old snapshot, and `unavailable` when nothing could be read. A `remaining_percent` of `null` means the value could not be read; the reason is in `notes` or in `providers.<name>.error`. `providers` holds every quota window for each agent. `unmapped_buckets` lists quota windows that no agent follows, and `models` holds the model check (same format as `cue models --json`, or `null` when model lists were not fetched).

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
  "order": ["claude", "sol", "argon"],    // who goes first; omit to use the default
  "min_remaining_percent": 20,            // agents below this move to the back
  "timeout_minutes": 30,
  "handover_on": "quota",                 // quota: hand over only when quota runs out; any_failure: on any failure
  "success_file": "summaries/latest.md"   // optional: the run counts as a success only if this file was written
}
```

Agents with at least `min_remaining_percent` left go first, in order. Agents that are low or whose quota could not be read go after them, and exhausted agents are skipped. A handover happens when the agent's output contains a quota-exhausted message, or when a failed run is followed by a quota check that shows the agent is out.

Exit codes: `0` success, `1` the task failed for a reason other than quota, `2` no agent had quota, `3` configuration error or the same task is already running. Every run is recorded in `data/relay/history.jsonl`, and each agent's full output is saved in `data/relay/logs/`.

The models the relay uses are listed in `runners.json` in the repository. The default order uses three models of the same tier, all at medium reasoning effort:

| Order | Agent | Platform | Model | What it can do unattended |
|---|---|---|---|---|
| 1 | `claude` | Claude Code | `claude-opus-5-5` | Read and write files in the working folder; shell commands that need approval are denied |
| 2 | `sol` | Codex CLI | `gpt-6.1-sol` | Run commands in a sandbox that can only write the working folder |
| 3 | `argon` | Antigravity `agy` | `gemini-4-argon-medium` | Read and write files; shell commands are denied |

The three draw on separate quotas, so when one runs out the other two can still take over.

`runners.json` also defines `luna` (Codex `gpt-6-luna`) and `flash` (agy `gemini-3.8-flash-medium`), which are not in the default order: Luna shares the Codex quota with Sol, and Flash cannot do the work of the other three. Add them to a task's `order` if you want them.

If a model no longer exists (renamed or retired), each CLI returns a specific error. `cue relay` recognizes these errors, records the attempt as "model missing", hands the task to the next agent, and asks you to run `cue models`.

## Changing models (runners.json and cue models)

Platforms add, rename, and retire models over time. CueSheet keeps what it reads live separate from what you decide:

| What | Where it comes from |
|---|---|
| Quota and reset times on all three platforms | Queried from each CLI on every run; quotas for new models appear automatically |
| Which models share each Gemini group | Read live from the `agy` response, for example "Gemini 組（Hydrogen、Flash）" |
| Models each platform offers now | Queried live by `cue models`: Claude Code `initialize`, Codex `model/list`, and `agy models` |
| Which model and effort the relay uses | `runners.json`, set by you |
| Which quota windows each agent follows | `agents.json`, set by you |

### runners.json format

```json
{
  "default_order": ["claude", "sol", "argon"],
  "runners": {
    "claude": {"platform": "claude", "model": "claude-opus-5-5", "effort": "medium", "family": "opus"},
    "sol": {"platform": "codex", "model": "gpt-6.1-sol", "effort": "medium", "family": "sol"},
    "argon": {"platform": "agy", "model": "gemini-4-argon-medium", "effort": "medium", "family": "argon"}
  }
}
```

- `platform`: `claude`, `codex`, or `agy`. CueSheet builds the unattended command for that platform.
- `model`: the model id, exactly as `cue models` lists it.
- `effort`: reasoning effort, optional. `agy` model ids already include an effort, such as `-medium`.
- `family`: the model family, which `cue models` uses to find newer versions.
- Your `relay.json` can also contain `runners`. List only the fields to change; the rest comes from `runners.json`. For example, to run one task on Fable: `"runners": {"claude": {"model": "claude-fable-5-2"}}`.

### Checking and updating

```powershell
.\cue.ps1 models            # list models on all three platforms and check each model in runners.json
.\cue.ps1 models --update   # write newer versions from the same family back to runners.json
.\cue.ps1 models --json     # print JSON
```

Each relay model gets one of four states: `正常` (ok), `可升級` (a newer version of the same family exists), `不存在` (missing; the newest version of the same family is shown), and `未知` (the platform's model list could not be read). For Codex models, the reasoning effort is checked against the efforts the model supports.

`--update` only moves to a newer version of the same family, for example `gpt-6-sol` to `gpt-6.1-sol` or `claude-opus-4-8` to `claude-opus-5-5`. The family and version come from the model id (`claude-<family>-<version>`, `gpt-<version>-<family>`, `gemini-<version>-<family>-<effort>`), and the highest version wins. The changes are printed, and `runners.json` is in Git, so a wrong change can be reverted.

### Alerts during normal quota checks

Each `cue` run also fetches the model lists in the background, at no quota cost and in parallel with the quota check. The "需要注意" (needs attention) section at the end lists:

- Quota windows that no agent follows, usually a new model's own quota (such as Claude's weekly Fable quota).
- New models. CueSheet records the models it has seen in `data/models_seen.json` and flags a model for 7 days after it first appears. The first run only records the baseline.
- Relay models that are missing, can be upgraded, or do not support the configured effort.

Add `--no-models` to skip the model lists. They are also skipped with `--agent` or `--only`.

### Updating CueSheet yourself

1. Run `.\cue.ps1 models` and look for relay models marked `不存在` or `可升級`.
2. For a newer version of the same family, run `.\cue.ps1 models --update`.
3. To switch to a different family (for example Argon to Hydrogen), edit `model` and `family` in `runners.json`, copying the model id from `cue models`.
4. If a new model has its own quota, add an agent to `agents.json` with the quota id from `providers` in `cue --json`. To make a relay runner follow that quota, add `"quota_agent": "<agent id>"` to its entry in `runners.json`.
5. Run `.\cue.ps1 relay <task> --dry-run` to check the command.

### Not done yet

`cue models` only finds newer versions within a family. Deciding which models in different families are of the same tier (for example, whether Hydrogen can replace Argon) is still up to you. A more reliable approach is to match models by score ranges on public leaderboards or benchmarks, which is the planned next step.

Things to keep in mind when writing tasks:

- The agent that takes over does not know how far the previous one got and starts again. Tasks that write their result to a file are safe to run twice.
- Nobody is there to approve tools, so tools that need approval are denied. `agy` still exits with code 0 when this happens; `cue relay` recognizes the message and records a permission problem. Tasks that only read and write files are the most reliable.
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

`agents.json` maps agents to quota windows and sets the low and exhausted thresholds. The included agents are `claude`, `sol`, `luna`, `argon`, `flash`, and `agy-3p`. Windows listed under `buckets` must exist in the provider's output; windows under `optional_buckets` count only when present.

`runners.json` sets the relay models and the default order; see "Changing models" above.

If a CLI is not on `PATH`, copy `config.example.json` to `config.json` and set its location. Git ignores `config.json`.

## Development

```powershell
python -m unittest discover -s tests
```

The tests parse recorded CLI responses and model lists, and the relay tests simulate exhausted quota with fake agents. No AI CLI is called. Each agent has its own module in `cuesheet/` (`claude.py`, `codex.py`, `gemini.py`), so a change in one CLI's output usually needs a fix in one file.

## Related projects

[CodexBar](https://github.com/steipete/CodexBar) is a macOS menu bar app covering many providers. [caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) is a cross-platform Rust CLI. [cclimits](https://github.com/cruzanstx/cclimits) is a CLI for macOS and Linux. [Token Monitor](https://github.com/Javis603/token-monitor) is a desktop widget. Several of these read OAuth tokens or browser cookies and call provider APIs directly. CueSheet runs each CLI's own read-only command and reports which quota window limits each agent.

## License

[MIT](LICENSE)
