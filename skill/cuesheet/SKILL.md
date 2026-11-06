---
name: cuesheet
description: 派工前先看額度。要把工作交給 Codex／Luna（gpt-6-luna）、agy／Gemini（Argon、Flash）或其他本機 AI 代理之前，或使用者問「還剩多少額度」「該用哪個模型」「誰還有額度」時使用。執行 CueSheet 讀三端（Claude Code、Codex、agy）的即時額度，依剩餘量選代理。
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
| 大量、指示明確的查核、整理、比對 | `flash`（agy `gemini-3.8-flash-medium`） | `luna` |
| 需要深度推理的查重、資料驗收 | `luna`（Codex `gpt-6-luna`） | `flash` 拆小批，或留給 `claude` |
| 發散、討論、第二意見 | `argon`（agy `gemini-4-argon-medium`） | `flash` |
| 整合、寫檔、最後判斷 | `claude`（主控自己） | 先告訴使用者 |

規則：
- `level` 是 `exhausted` 的代理不要派工。改派替代代理，並在回覆裡說一句為什麼換。
- `low`（低於 20%）的代理只派短任務。大任務改派替代代理，或等 `resets_at` 之後再派。
- Argon 同時受「Gemini 組」與「Argon 專屬週額度」限制，Flash 只受 Gemini 組限制。Flash 比 Argon 便宜，同樣額度能做更多事。
- `agy-3p`（agy 裡的 Claude／GPT-OSS）額度很少，除非使用者指定，否則不用。
- `claude` 偏低時要先告訴使用者，因為主控停了，整個流程都會停。

## 3. 回報

派工前用一行告訴使用者選了誰、剩多少，例如：

> 派給 Flash（Gemini 組剩 95%）。Luna 本週只剩 11%，留給需要深推理的部分。

重置時間要換成本機時間（台灣 UTC+8）再講。

## 注意

- 不要使用 Codex 的「額度全重置」點數。CueSheet 只顯示剩幾次，要不要用由使用者自己決定。
- 在 Git Bash 裡手動打 `agy -p "/usage"` 會被改寫成路徑、變成一般提問（會花額度）。要查 agy 額度一律用 CueSheet。
