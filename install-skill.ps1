# 把 CueSheet 的 Skill 安裝到 Claude Code 個人 Skill 資料夾（~/.claude/skills/cuesheet）
# 用法：.\install-skill.ps1
$src = Join-Path $PSScriptRoot "skill\cuesheet\SKILL.md"
$dstDir = Join-Path $env:USERPROFILE ".claude\skills\cuesheet"
New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
$text = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)
$text = $text.Replace("{{CUESHEET_HOME}}", $PSScriptRoot)
[System.IO.File]::WriteAllText((Join-Path $dstDir "SKILL.md"), $text, (New-Object System.Text.UTF8Encoding $false))
Write-Host "已安裝到 $dstDir（CueSheet 路徑：$PSScriptRoot）"
