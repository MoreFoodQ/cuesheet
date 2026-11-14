---
name: cuesheet
description: 派工前先看額度。要把工作交給 Codex（Sol gpt-6.1-sol、Luna gpt-6-luna）、agy／Gemini（Argon、Flash）或其他本機 AI 代理之前，或使用者問「還剩多少額度」「該用哪個模型」「誰還有額度」時使用。執行 CueSheet 讀三端（Claude Code、Codex、agy）的即時額度，依剩餘量選代理。
---

# CueSheet：派工前看額度

## 1. 讀額度

```powershell
python "{{CUESHEET_HOME}}\cue.py" --max-age 600 --json
```

- 10 分鐘內查過就用快取，不會重查；要最新數字就拿掉 `--max-age 600`（約 10～20 秒）。
- 只看 `agents` 陣列：每個代理有 `remaining_percent`（相關額度裡剩最少的那個）、`level`（`ok`／`low`／`exhausted`／`unknown`）、`resets_at`（UTC）、`notes`。
- `remaining_percent` 為 `null` 代表**讀不到**，不是 0。不要自己猜數字，照 `notes` 講原因。
- 只想確認單一代理：`python "{{CUESHEET_HOME}}\cue.py" --agent luna --max-age 600`，結束碼 0 正常、1 偏低、2 用完、3 未知。

## 2. 選代理

依 `agents.json` 的 id：

| 工作類型 | 首選 | 首選偏低或用完時 |
|---|---|---|
| 大量、規則明確、不需要判斷的整理（目錄掃描、欄位整理） | `flash`（agy `gemini-3.8-flash-medium`） | `sol` |
| 需要深度推理的查重、資料驗收 | `luna`（Codex `gpt-6-luna`） | `sol` |
| 發散、討論、第二意見 | `argon`（agy `gemini-4-argon-medium`） | `sol` |
| 整合、寫檔、最後判斷 | `claude`（主控自己） | 先告訴使用者 |

替補只找同等級的模型：Claude Opus 5.5 medium、Sol（Codex `gpt-6.1-sol` medium）、Argon medium。三者互為替補。

規則：
- Flash 不當任何代理的替補。它只接本來就適合它的簡單工作；別人額度不夠時，不要降級改派 Flash。
- `level` 是 `exhausted` 的代理不要派工。改派同等級的替補，並在回覆裡說一句為什麼換。
- `low`（低於 20%）的代理只派短任務。大任務改派同等級的替補，或等 `resets_at` 之後再派。
- Sol 和 Luna 共用 Codex 的 `codex` 額度，一個偏低另一個也一樣，不能互相替補。
- Argon 同時受「Gemini 組」與「Argon 專屬週額度」限制。
- `agy-3p`（agy 裡的 Claude／GPT-OSS）額度很少，除非使用者指定，否則不用。
- `claude` 偏低時要先告訴使用者，因為主控停了，整個流程都會停。

## 3. 回報

派工前用一行告訴使用者選了誰、剩多少，例如：

> 派給 Sol（Codex 剩 54%）。Argon 本週專屬額度只剩 5%，這次先跳過。

## 4. 排程任務

排程或無人看管的任務用 `cue relay`，不要自己寫重試邏輯。預設接力順序是 `claude` → `sol` → `argon`，額度用完會自動換下一位。說明見 CueSheet 的 README「排程接力」一節。

重置時間要換成本機時間（台灣 UTC+8）再講。

## 注意

- 不要使用 Codex 的「額度全重置」點數。CueSheet 只顯示剩幾次，要不要用由使用者自己決定。
- 在 Git Bash 裡手動打 `agy -p "/usage"` 會被改寫成路徑、變成一般提問（會花額度）。要查 agy 額度一律用 CueSheet。
