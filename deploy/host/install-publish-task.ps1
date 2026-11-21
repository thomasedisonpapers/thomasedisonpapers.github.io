# Registers publish-to-site.ps1 as the EdisonSitePublish scheduled task, built like EdisonReview: your own session,
# no stored password, and never a second copy alongside a running one.
#   powershell -ExecutionPolicy Bypass -File deploy\host\install-publish-task.ps1
param(
    # How often to look for edits, reviews and exports; a run with nothing new uploads nothing.
    [ValidateRange(5, 1440)]
    [int]$EveryMinutes = 15
)
$ErrorActionPreference = "Stop"

$runner = Join-Path $PSScriptRoot "publish-to-site.ps1"
foreach ($needed in @((Join-Path $HOME "edison-pipeline\.venv\Scripts\python.exe"), (Join-Path $HOME "edison-publisher\src"),
                      (Join-Path $HOME "edison-archive\.venv\Scripts\python.exe"), $runner)) {
    if (-not (Test-Path $needed)) { throw "$needed not found; see docs/DEPLOY.md, 'Publishing from the host'." }
}

Unregister-ScheduledTask -TaskName "EdisonSitePublish" -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`"" -WorkingDirectory $HOME
$account = "$env:USERDOMAIN\$env:USERNAME"
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
# No time limit: the first run publishes and uploads every folder; later runs take a minute or two.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive

Register-ScheduledTask -TaskName "EdisonSitePublish" -Action $action -Trigger $repeat -Settings $settings -Principal $principal `
    -Description "Edison Papers: publishes drafts and exports to the public site's dataset every $EveryMinutes minutes" | Out-Null

Write-Host "Registered EdisonSitePublish (every $EveryMinutes minutes)." -ForegroundColor Green
Write-Host "Watch it:  Get-Content `$HOME\edison-site\logs\publish-$(Get-Date -Format yyyy-MM-dd).log -Tail 20 -Wait"
Write-Host "Pause it:  Disable-ScheduledTask EdisonSitePublish"
