# ==============================================================
# PowerShell Script to register Daily 8:30 AM Job Crawler Task
# ==============================================================

$TaskName = "DailyJobCrawler_Sarthak"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PythonPath = (Get-Command python).Source
$MainScript = Join-Path $ScriptDir "main.py"
$TriggerTime = "08:30AM"

Write-Host "Creating Scheduled Task: $TaskName" -ForegroundColor Cyan
Write-Host "Script: $MainScript"
Write-Host "Schedule: Daily at $TriggerTime"

$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument "`"$MainScript`"" -WorkingDirectory $ScriptDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Daily 8:30 AM Job Crawler & Matcher for Sarthak Joshi" -Force

Write-Host "`nTask '$TaskName' registered successfully!" -ForegroundColor Green
Write-Host "To test run immediately, execute: Start-ScheduledTask -TaskName '$TaskName'"

