# ServerWatch on Windows: installs what it needs on first run, then watches this PC over WMI.
# In an Administrator PowerShell, from this folder:
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\start.ps1                                 watch this PC, service Spooler
#   .\start.ps1 --services Spooler,W32Time      extra arguments go to agent\serverwatch.py
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py -or $py.Source -like "*WindowsApps*") {
    Write-Host "Python not found. Install Python 3.9+ from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'." -ForegroundColor Red
    exit 1
}
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Warning "Not an Administrator PowerShell: FIX (StartService) and the firewall rule will not work."
}

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "Creating .venv ..."
    python -m venv .venv
}
.\.venv\Scripts\python -m pip install -q --disable-pip-version-check -r agent\requirements.txt

# Let the beacon and other PCs reach the dashboard
if ($admin -and -not (Get-NetFirewallRule -DisplayName "ServerWatch agent 8080" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "ServerWatch agent 8080" -Direction Inbound -Protocol TCP `
        -LocalPort 8080 -Action Allow -Profile Any | Out-Null
    Write-Host "Firewall: allowed inbound TCP 8080"
}

$agentArgs = if ($args.Count -gt 0) { $args } else { @("--services", "Spooler") }
.\.venv\Scripts\python agent\serverwatch.py @agentArgs
