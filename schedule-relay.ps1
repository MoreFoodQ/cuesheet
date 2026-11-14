<#
把 relay.json 裡的任務排進 Windows 工作排程器，每天固定時間執行 cue relay。

用法：
  .\schedule-relay.ps1 -Task daily-notes -At 09:00      每天 09:00 執行
  .\schedule-relay.ps1 -Task daily-notes -Remove        取消排程
  .\schedule-relay.ps1 -List                            列出已排的 CueSheet 任務

排程只在你登入 Windows 時執行，因為 claude、codex、agy 的登入狀態都存在你的使用者帳號下。
#>
param(
    [string]$Task,
    [string]$At = "09:00",
    [switch]$Remove,
    [switch]$List
)

$prefix = "CueSheet relay - "

if ($List) {
    Get-ScheduledTask | Where-Object { $_.TaskName -like "$prefix*" } |
        Select-Object TaskName, State, @{n = "NextRun"; e = { (Get-ScheduledTaskInfo $_).NextRunTime } }
    exit 0
}
if (-not $Task) { Write-Host "請用 -Task 指定任務名稱（relay.json 裡的名字）"; exit 3 }

$name = "$prefix$Task"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "已取消排程：$name"
    exit 0
}

$python = (Get-Command python -ErrorAction Stop).Source
$cue = Join-Path $PSScriptRoot "cue.py"
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$cue`" relay $Task" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "CueSheet：依額度接力執行 $Task" -Force | Out-Null
Write-Host "已排程：每天 $At 執行 $Task（工作名稱：$name）"
Write-Host "執行紀錄在 data\relay\history.jsonl，各代理輸出在 data\relay\logs\"
