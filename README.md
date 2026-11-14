<p align="center">
  <img src="assets/banner.png" alt="CueSheet" width="820">
</p>

<p align="center">
  <a href="https://github.com/MoreFoodQ/cuesheet/actions/workflows/test.yml"><img src="https://github.com/MoreFoodQ/cuesheet/actions/workflows/test.yml/badge.svg" alt="test"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license">
</p>

<p align="center">繁體中文 | <a href="README.en.md">English</a></p>

CueSheet 是一個命令列工具，列出同一台電腦上三個 AI 程式代理的剩餘額度：Claude Code、Codex CLI，以及 Antigravity 的 CLI（`agy`）。它會印出每個額度時間窗和重置時間，並把同樣的資料寫成 JSON，讓腳本或其他 AI 代理在派工前先查額度。排程任務可以交給 `cue relay`：它會挑有額度的代理執行，跑到一半額度用完就換下一位接手。

## 顯示哪些額度

| 代理 | 額度時間窗 |
|---|---|
| Claude Code | 5 小時、每週；方案有分模型週額度時也會列出 |
| Codex CLI | `codex` 這組的 5 小時與每週，以及免費「額度全重置」點數的數量 |
| Antigravity `agy` | Gemini 組、Claude／GPT 組各自的 5 小時與每週，以及 Pro 方案中 Gemini 4 Argon 的獨立週額度 |

`agents.json` 把每個代理對應到一個或多個時間窗。CueSheet 取其中剩最少的值，並標出是哪個時間窗。例如 Gemini 4 Argon 同時受 Gemini 組和它自己的週額度限制。

## 需求

- Windows 10 以上。測試也會在 Linux 上跑，但實際呼叫 CLI 的部分只在 Windows 測過。
- Python 3.10 以上，不需要安裝任何套件。
- 要查的 CLI 需要先安裝並登入：`claude`、`codex`、`agy`。沒裝的那一個會顯示「無法取得」，不影響另外兩個。

## 安裝

```powershell
git clone https://github.com/MoreFoodQ/cuesheet.git
cd cuesheet
```

## 用法

```powershell
.\cue.ps1                  # 查全部代理、印表格、寫入 data\latest.json
.\cue.ps1 --json           # 改印 JSON
.\cue.ps1 --only codex     # 只查部分代理：claude、codex、gemini
.\cue.ps1 --max-age 600    # data\latest.json 未滿 600 秒就直接沿用
.\cue.ps1 --agent luna     # 只看一個代理，並設定結束碼
```

`python cue.py` 接受同樣的參數。三個 CLI 同時查詢，一次完整查詢約 10 到 20 秒。

輸出範例：

```
■ 派工參考（剩餘＝該代理相關額度裡最少的那一個）
  狀態    代理                      剩餘  卡在                          重置
  [正常]  Claude Code（主控）       61%   每週（全部模型）              11/11 04:00（4 天 6 小時後）
  [偏低]  Luna（Codex gpt-6-luna）  11%   codex 每週                    11/09 23:18（3 天 1 小時後）
  [正常]  Gemini 4 Argon（agy）     28%   Argon 專屬 每週               11/09 22:43（3 天 1 小時後）
  [正常]  Gemini 3.8 Flash（agy）   95%   Gemini 組（Flash＋Pro） 每週  11/09 21:25（2 天 23 小時後）
  [用完]  agy 內的 Claude／GPT-OSS  0%    Claude／GPT 組 每週           11/09 22:43（3 天 1 小時後）
```

### 結束碼

使用 `--agent` 時，結束碼代表該代理的狀態：`0` 正常、`1` 偏低（低於 20%）、`2` 用完（1% 以下）、`3` 未知。門檻在 `agents.json` 設定。

```powershell
.\cue.ps1 --agent luna --max-age 600
if ($LASTEXITCODE -ge 2) { "Luna 額度用完了" }
```

## JSON 輸出

每次查詢都會寫入 `data/latest.json`，這個檔案不進 Git。只需要派工判斷的腳本讀 `agents` 陣列即可：

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

時間一律是 UTC。`level` 是 `ok`、`low`、`exhausted`、`unknown` 其中之一。`data_status` 為 `ok` 表示即時資料，`stale` 表示舊快照，`unavailable` 表示讀不到。`remaining_percent` 為 `null` 代表讀不到數值，原因寫在 `notes` 或 `providers.<名稱>.error`。`providers` 裡有每個代理的所有時間窗。`unmapped_buckets` 是沒有代理在看的額度，`models` 是模型清單的檢查結果（格式同 `cue models --json`；沒有抓模型時是 `null`）。

## Claude Code Skill

`skill/cuesheet/SKILL.md` 讓 Claude Code 在把工作交給 Codex 或 `agy` 之前先查額度，並附一張依工作類型選代理的表。安裝方式：

```powershell
.\install-skill.ps1
```

腳本會把 Skill 複製到 `~/.claude/skills/cuesheet/`，並填入這個 repo 的路徑。

## 排程接力（cue relay）

`cue relay` 照額度決定排程任務由誰執行。任務寫在 `relay.json`，每個任務有提示詞、工作資料夾和代理順序。執行時先查額度，跳過已經用完的代理，交給順序上第一個有額度的人；它跑到一半額度用完，就換下一位重新跑。`cue relay` 本身不是 AI，不消耗額度。

```powershell
copy relay.example.json relay.json        # 第一次使用：複製範例再編輯
.\cue.ps1 relay --list                    # 列出任務
.\cue.ps1 relay daily-notes --dry-run     # 只顯示會派給誰，不執行
.\cue.ps1 relay daily-notes               # 執行
.\schedule-relay.ps1 -Task daily-notes -At 09:00   # 排進 Windows 工作排程器，每天 09:00
```

任務設定：

```jsonc
"daily-notes": {
  "workdir": "C:\\path\\to\\project",
  "prompt": "讀 notes 裡今天修改過的 .md 檔，整理成摘要，寫到 summaries/YYYY-MM-DD.md。",
  "order": ["claude", "sol", "argon"],    // 上場順序；不寫就用預設
  "min_remaining_percent": 20,            // 低於這個比例的代理排到後面
  "timeout_minutes": 30,
  "handover_on": "quota",                 // quota：只有額度用完才換人；any_failure：任何失敗都換人
  "success_file": "summaries/latest.md"   // 選用：這個檔案有被寫出才算成功
}
```

選人的規則：剩餘量達到 `min_remaining_percent` 的代理照順序先上；偏低或讀不到額度的排在後面；已用完的不上場。換人有兩種判斷：輸出裡出現額度用完的訊息，或失敗後重查額度發現已經用完。

結束碼：`0` 成功、`1` 任務本身失敗（不是額度問題）、`2` 沒有代理有額度、`3` 設定錯誤或同一任務正在執行。每次執行記在 `data/relay/history.jsonl`，各代理的完整輸出存在 `data/relay/logs/`。

接力用哪些模型，寫在 repo 裡的 `runners.json`。預設順序是三家同等級的模型，推理強度都是 medium：

| 順序 | 代理 | 平台 | 模型 | 無人模式下能做什麼 |
|---|---|---|---|---|
| 1 | `claude` | Claude Code | `claude-opus-5-5` | 讀寫工作資料夾裡的檔案；需要核准的 shell 指令會被拒絕 |
| 2 | `sol` | Codex CLI | `gpt-6.1-sol` | 在沙盒裡執行指令，只能寫工作資料夾 |
| 3 | `argon` | Antigravity `agy` | `gemini-4-argon-medium` | 讀寫檔案；shell 指令會被拒絕 |

三家的額度互不相干，所以一家用完，另外兩家還能接手。

`runners.json` 另外有 `luna`（Codex `gpt-6-luna`）和 `flash`（agy `gemini-3.8-flash-medium`），但不在預設順序裡：Luna 和 Sol 共用 Codex 的額度，Flash 的能力替代不了其他三個。需要的話，在任務的 `order` 裡自己加。

接力時如果模型不存在（改名或下架），三家都會回特定的錯誤訊息。`cue relay` 認得這些訊息，會把這一棒記成「模型不存在」並換下一位，同時提醒你執行 `cue models`。

## 換模型（runners.json 與 cue models）

各平台會不定期推出新模型、把舊的改名或下架。CueSheet 把「哪些是即時抓的」和「哪些要你決定」分開：

| 內容 | 怎麼來的 |
|---|---|
| 三家的額度與重置時間 | 每次查詢時向 CLI 呼叫取得，新模型的獨立額度會自動出現 |
| Gemini 各組包含哪些模型 | 從 `agy` 的回應即時讀出（例如「Gemini 組（Hydrogen、Flash）」） |
| 三家目前提供的模型清單 | `cue models` 即時呼叫：Claude Code 的 `initialize`、Codex 的 `model/list`、`agy models` |
| 接力用哪個模型、哪個推理強度 | `runners.json`，由你決定 |
| 哪個代理看哪幾個額度 | `agents.json`，由你決定 |

### runners.json 的格式

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

- `platform`：`claude`、`codex` 或 `agy`。CueSheet 依平台組出無人模式的指令。
- `model`：模型 id，要跟 `cue models` 列出來的一樣。
- `effort`：推理強度，可省略。`agy` 的模型 id 本身就帶強度（例如 `-medium`）。
- `family`：系列名稱，`cue models` 用它找同系列的新版本。
- 自己的 `relay.json` 也可以寫 `runners`，只寫要改的欄位就好，其餘沿用 `runners.json`。例如只想讓某個任務改用 Fable：`"runners": {"claude": {"model": "claude-fable-5-2"}}`。

### 檢查與更新

```powershell
.\cue.ps1 models            # 列出三家模型，檢查 runners.json 的每個模型
.\cue.ps1 models --update   # 把同系列較新的版本寫回 runners.json
.\cue.ps1 models --json     # 改印 JSON
```

每個接力模型會顯示四種狀態：`正常`、`可升級`（同系列有更新的版本）、`不存在`（平台上找不到，會列出同系列最新的版本）、`未知`（讀不到那家的模型清單）。Codex 的模型也會檢查推理強度是否支援。

`--update` 只會換成同系列的新版本，例如 `gpt-6-sol` 換成 `gpt-6.1-sol`、`claude-opus-4-8` 換成 `claude-opus-5-5`。系列的判斷方式是從模型 id 拆出系列名和版本號（`claude-<系列>-<版本>`、`gpt-<版本>-<系列>`、`gemini-<版本>-<系列>-<強度>`），版本最大的就是最新版。改完會在畫面上列出改了哪些，`runners.json` 在 Git 裡，改錯可以還原。

### 平常查額度時的提醒

每次執行 `cue`，都會在背景順便抓一次模型清單（不花額度，和查額度同時進行）。以下情況會列在最後的「需要注意」：

- 有額度沒有代理在看，通常是新模型的獨立額度（例如 Claude 的 Fable 週額度）。
- 有新模型出現。CueSheet 把看過的模型記在 `data/models_seen.json`，第一次看到的模型會提醒 7 天。第一次執行只建立紀錄，不會提醒。
- 接力用的模型不存在、可以升級，或推理強度不支援。

不想抓模型清單時加 `--no-models`。只查單一代理（`--agent`）或部分平台（`--only`）時也不會抓。

### 沒有人更新 CueSheet 時，自己怎麼改

1. 執行 `.\cue.ps1 models`，看哪個接力模型是 `不存在` 或 `可升級`。
2. 同系列的新版本：執行 `.\cue.ps1 models --update`。
3. 想換成別的系列（例如 Argon 換成 Hydrogen）：直接編輯 `runners.json` 的 `model` 和 `family`，模型 id 照 `cue models` 列出來的抄。
4. 新模型有自己的額度時：在 `agents.json` 加一個代理，`buckets` 填 `cue --json` 裡 `providers` 列出的額度 id；如果要讓接力看這個額度，在 `runners.json` 那一筆加 `"quota_agent": "<代理 id>"`。
5. 執行 `.\cue.ps1 relay <任務> --dry-run` 確認指令正確。

### 還沒做到的部分

`cue models` 目前只會找「同系列」的新版本。不同系列之間誰跟誰同等級（例如 Hydrogen 是不是可以取代 Argon），還要你自己判斷。比較可靠的做法是參考公開的模型排行榜或基準測試分數，用分數區間自動配對同等級的模型，這是之後要做的方向。

寫任務時要注意：

- 接手的代理不知道上一位做到哪裡，會從頭再跑一次。任務最好寫成「把結果寫到某個檔案」，重跑也不會出錯。
- 無人模式下沒有人能按「允許」，需要核准的工具會被自動拒絕。`agy` 被拒絕時結束碼仍是 0，`cue relay` 會從錯誤輸出辨認出來，記成權限問題。只需要讀寫檔案的任務最穩。
- 排程只在你登入 Windows 時執行，因為三家 CLI 的登入狀態都存在你的使用者帳號下。

## 額度怎麼讀取

每個代理都透過它自己 CLI 的唯讀指令查詢，不會開始模型回合，也不會消耗額度。CueSheet 不讀取、不儲存、不傳送任何登入憑證，登入由各 CLI 自己處理。CueSheet 會讀寫哪些東西，列在 [SECURITY.md](SECURITY.md)。

| 代理 | 指令 | 限制 |
|---|---|---|
| Claude Code | `claude -p --input-format stream-json` 搭配 `get_usage` 控制請求 | 這是沒有公開文件的內部協定，Claude Code 更新後可能改變。查詢時加上 `--no-session-persistence`，不會留下 session 檔。 |
| Codex CLI | `codex app-server` 的 `account/rateLimits/read` 方法 | app server 標示為 experimental。呼叫失敗時，CueSheet 改讀 `~/.codex/sessions` 裡最後一筆 `rate_limits`，並標成 `stale`；快照之後已經過了重置時間的時間窗會顯示為未知。 |
| Antigravity `agy` | `agy -p "/usage" --output-format json` | 只提供剩餘比例。舊版 `agy` 會把 `/usage` 當成提問送給模型；CueSheet 會從 `num_turns` 偵測，並把該代理標為無法取得。 |

在 Git Bash 裡手動執行 `agy -p "/usage"` 會把提問送給模型。MSYS 的路徑轉換會在 `agy` 收到參數前，把 `/usage` 改成 `C:/Program Files/Git/usage`。請改在 PowerShell 執行。CueSheet 直接傳遞參數，不受影響。

Codex app server 另有一個 `account/rateLimitResetCredit/consume` 方法，會用掉一次重置點數。CueSheet 不會呼叫它。

在本機的 Codex session 紀錄裡，所有模型（包括 `gpt-6-luna`）都記在 `codex` 這組額度下。OpenAI 沒有公開說明這一點。

最後測試版本：Claude Code 2.1.304、codex-cli 0.164.0、`agy`（2026-11-06）。

## 設定

`agents.json` 設定代理和時間窗的對應，以及偏低、用完的門檻。內建的代理是 `claude`、`sol`、`luna`、`argon`、`flash`、`agy-3p`。列在 `buckets` 的時間窗必須存在；列在 `optional_buckets` 的時間窗有出現才計算。

`runners.json` 設定接力用的模型與預設順序，格式見上方「換模型」一節。

CLI 不在 `PATH` 裡時，把 `config.example.json` 複製成 `config.json` 並填入路徑。`config.json` 不進 Git。

## 開發

```powershell
python -m unittest discover -s tests
```

測試使用 `tests/fixtures` 裡錄下的 CLI 回應和模型清單，接力的測試用假的代理模擬額度用完，都不會呼叫任何 AI CLI。每個代理在 `cuesheet/` 有自己的模組（`claude.py`、`codex.py`、`gemini.py`），某個 CLI 的輸出改變時，通常只需要修一個檔案。

## 相關專案

[CodexBar](https://github.com/steipete/CodexBar) 是支援很多服務的 macOS 選單列程式。[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) 是跨平台的 Rust CLI。[cclimits](https://github.com/cruzanstx/cclimits) 是 macOS 與 Linux 的 CLI。[Token Monitor](https://github.com/Javis603/token-monitor) 是桌面小工具。其中幾個會讀取 OAuth 權杖或瀏覽器 cookie，直接呼叫服務商的 API。CueSheet 則是執行各 CLI 自己的唯讀指令，並標出每個代理被哪個時間窗限制。

## 授權

[MIT](LICENSE)
