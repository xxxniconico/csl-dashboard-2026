# Register / remove Windows Task Scheduler job for daily CSL data refresh.
# Default 09:30 local time (next morning after night matches).
#
# Register:   powershell -ExecutionPolicy Bypass -File .\register_csl_daily_update.ps1
# Custom time: .\register_csl_daily_update.ps1 -Time "08:00"
# Remove:      .\register_csl_daily_update.ps1 -Unregister

[CmdletBinding()]
param(
    [string] $Time = "09:30",
    [string] $TaskName = "CSL_Project_v2_DailyUpdate",
    [switch] $Unregister
)

$ErrorActionPreference = "Stop"
$CmdPath = Join-Path $PSScriptRoot "run_csl_update.cmd"

if (-not $Unregister -and -not (Test-Path $CmdPath)) {
    throw "Missing: $CmdPath"
}

if ($Unregister) {
    schtasks /Delete /TN $TaskName /F 2>$null
    Write-Host "Deleted task (if existed): $TaskName"
    exit 0
}

# /TR must be one quoted path; .cmd wraps PowerShell with correct cwd via %~dp0
$tr = "`"$CmdPath`""
$p = Start-Process -FilePath "schtasks.exe" -ArgumentList @(
    "/Create", "/TN", $TaskName,
    "/TR", $tr,
    "/SC", "DAILY", "/ST", $Time,
    "/RL", "LIMITED", "/F"
) -Wait -PassThru -NoNewWindow

if ($p.ExitCode -ne 0) {
    throw "schtasks failed exit=$($p.ExitCode). Try elevated PowerShell or check task name conflict."
}

Write-Host "Registered task: $TaskName"
Write-Host "  Daily at: $Time"
Write-Host "  Run: $CmdPath"
Write-Host "  Logs: (project)/logs/update_*.log"
