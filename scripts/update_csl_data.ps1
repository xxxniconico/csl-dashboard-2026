# CSL data pipeline for scheduled daily runs (next-day refresh after match nights).
# Usage: .\update_csl_data.ps1 [-SkipFixtures] [-SkipCrawler] [-Python "C:\Path\python.exe"]

[CmdletBinding()]
param(
    [switch] $SkipFixtures,
    [switch] $SkipCrawler,
    [string] $Python = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}
$LogFile = Join-Path $LogDir ("update_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-PythonCommand {
    if ($Python) {
        if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
        return @{ Exe = $Python; Prefix = @() }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return @{ Exe = $py.Source; Prefix = @() } }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) { return @{ Exe = $pyLauncher.Source; Prefix = @("-3") } }
    throw "Python 3 not found. Install Python or pass -Python path/to/python.exe"
}

function Invoke-PyStep {
    param(
        [string] $Label,
        [string[]] $PyArgs
    )
    Write-Log ("BEGIN " + $Label)
    $all = $script:PyCmd.Prefix + $PyArgs
    Write-Log ("EXE " + $script:PyCmd.Exe)
    $out = & $script:PyCmd.Exe @all 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $out) {
        $t = [string]$line
        Add-Content -Path $LogFile -Value $t -Encoding UTF8
        Write-Host $t
    }
    if ($code -ne 0) {
        Write-Log ("FAIL " + $Label + " exit=" + $code)
        throw ("Step failed: " + $Label)
    }
    Write-Log ("OK " + $Label)
}

$PyCmd = Get-PythonCommand
Write-Log "======== CSL data update ========"
Write-Log ("ProjectRoot=" + $ProjectRoot)
Write-Log ("Python=" + $PyCmd.Exe + " " + ($PyCmd.Prefix -join " "))

try {
    if (-not $SkipFixtures) {
        Invoke-PyStep "fixtures_fetcher" @(
            "src\crawler\csl_fixtures_fetcher.py",
            "--season", "2026"
        )
    } else {
        Write-Log "SKIP fixtures_fetcher"
    }

    if (-not $SkipCrawler) {
        $out = Join-Path $ProjectRoot "data\csl_matches_enriched_cfl.json"
        $ck = Join-Path $ProjectRoot "data\cfl_api_event_checkpoint.json"
        Invoke-PyStep "cfl_event_crawler" @(
            "src\crawler\cfl_api_event_crawler.py",
            "--output", $out,
            "--checkpoint", $ck,
            "--season", "2026",
            "--chunk-size", "10"
        )
    } else {
        Write-Log "SKIP cfl_event_crawler"
    }

    Invoke-PyStep "data_enricher" @("src\processor\data_enricher.py")
    Invoke-PyStep "web_renderer" @("src\renderer\web_renderer.py")

    Write-Log "======== Done ========"
    exit 0
} catch {
    Write-Log ("ERROR: " + $_.Exception.Message)
    exit 1
}
