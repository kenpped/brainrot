# daily.ps1 -- what the 8am scheduled task runs. Appends to out\daily-log.txt.
Set-Location $PSScriptRoot
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
Add-Content "out\daily-log.txt" "===== daily run $stamp ====="
& "$env:USERPROFILE\.venvs\brainrot\Scripts\python.exe" daily_run.py --count 5 *>> "out\daily-log.txt"
Add-Content "out\daily-log.txt" "===== exit $LASTEXITCODE ====="
