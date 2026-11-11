<p align="center">
  <img src="assets/banner.png" alt="CueSheet" width="820">
</p>

<p align="center">
  <a href="https://github.com/MoreFoodQ/cuesheet/actions/workflows/test.yml"><img src="https://github.com/MoreFoodQ/cuesheet/actions/workflows/test.yml/badge.svg" alt="test"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license">
</p>

<p align="center">繁體中文 | <a href="README.en.md">English</a></p>

CueSheet 是一個命令列工具，列出同一台電腦上三個 AI 程式代理的剩餘額度：Claude Code、Codex CLI，以及 Antigravity 的 CLI（`agy`）。它會印出每個額度時間窗和重置時間，並把同樣的資料寫成 JSON，讓腳本或其他 AI 代理在派工前先查額度。

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

時間一律是 UTC。`level` 是 `ok`、`low`、`exhausted`、`unknown` 其中之一。`data_status` 為 `ok` 表示即時資料，`stale` 表示舊快照，`unavailable` 表示讀不到。`remaining_percent` 為 `null` 代表讀不到數值，原因寫在 `notes` 或 `providers.<名稱>.error`。`providers` 裡有每個代理的所有時間窗。

## Claude Code Skill

`skill/cuesheet/SKILL.md` 讓 Claude Code 在把工作交給 Codex 或 `agy` 之前先查額度，並附一張依工作類型選代理的表。安裝方式：

```powershell
.\install-skill.ps1
```

腳本會把 Skill 複製到 `~/.claude/skills/cuesheet/`，並填入這個 repo 的路徑。

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

`agents.json` 設定代理和時間窗的對應，以及偏低、用完的門檻。內建的代理是 `claude`、`luna`、`argon`、`flash`、`agy-3p`。列在 `buckets` 的時間窗必須存在；列在 `optional_buckets` 的時間窗有出現才計算。

CLI 不在 `PATH` 裡時，把 `config.example.json` 複製成 `config.json` 並填入路徑。`config.json` 不進 Git。

## 開發

```powershell
python -m unittest discover -s tests
```

測試使用 `tests/fixtures` 裡錄下的 CLI 回應，不會呼叫任何 CLI。每個代理在 `cuesheet/` 有自己的模組（`claude.py`、`codex.py`、`gemini.py`），某個 CLI 的輸出改變時，通常只需要修一個檔案。

## 相關專案

[CodexBar](https://github.com/steipete/CodexBar) 是支援很多服務的 macOS 選單列程式。[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) 是跨平台的 Rust CLI。[cclimits](https://github.com/cruzanstx/cclimits) 是 macOS 與 Linux 的 CLI。[Token Monitor](https://github.com/Javis603/token-monitor) 是桌面小工具。其中幾個會讀取 OAuth 權杖或瀏覽器 cookie，直接呼叫服務商的 API。CueSheet 則是執行各 CLI 自己的唯讀指令，並標出每個代理被哪個時間窗限制。

## 授權

[MIT](LICENSE)
