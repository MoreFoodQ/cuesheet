[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
python "$PSScriptRoot\cue.py" @args
exit $LASTEXITCODE
