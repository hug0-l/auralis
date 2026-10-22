<#
.SYNOPSIS
    Start the Auralis development server (uvicorn, auto-reload) on port 8731.

.DESCRIPTION
    Runs the local FastAPI application that serves the Auralis web UI and the
    JSON / WebSocket API.  Works under PowerShell 7 and Windows PowerShell 5.1.

    The repository root is added to PYTHONPATH so that "aurserver" and
    "aurcore" import cleanly without an editable install.

.PARAMETER Port
    TCP port to listen on (default 8731).

.PARAMETER BindHost
    Interface to bind (default 127.0.0.1 — loopback only, by design).

.PARAMETER NoReload
    Disable the uvicorn auto-reload supervisor.

.EXAMPLE
    pwsh ./scripts/run_dev.ps1
    powershell -ExecutionPolicy Bypass -File ./scripts/run_dev.ps1 -Port 9000
#>
[CmdletBinding()]
param(
    [int]    $Port = 8731,
    [string] $BindHost = '127.0.0.1',
    [switch] $NoReload
)

$ErrorActionPreference = 'Stop'

# --- locate the repository root ---------------------------------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $RepoRoot

Write-Host ''
Write-Host '  Auralis — development server' -ForegroundColor DarkGreen
Write-Host "  repo   : $RepoRoot"
Write-Host ''

# --- pick a Python interpreter (>= 3.11) ------------------------------------
function Resolve-AuralisPython {
    $candidates = @()
    foreach ($name in @('py', 'python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }
    foreach ($exe in $candidates) {
        $prefix = @()
        if ([System.IO.Path]::GetFileNameWithoutExtension($exe) -eq 'py') { $prefix = @('-3') }
        & $exe @prefix -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{ Exe = $exe; Prefix = $prefix }
        }
    }
    return $null
}

$Python = Resolve-AuralisPython
if (-not $Python) {
    Write-Error 'Python 3.11 or newer was not found on PATH. Install it and retry.'
    exit 1
}

# --- make the repository importable ----------------------------------------
$sep = [System.IO.Path]::PathSeparator
$existing = [System.Environment]::GetEnvironmentVariable('PYTHONPATH')
if ([string]::IsNullOrEmpty($existing)) {
    $env:PYTHONPATH = $RepoRoot
} else {
    $env:PYTHONPATH = "$RepoRoot$sep$existing"
}

# --- report a missing server module instead of failing obscurely ------------
$appModule = Join-Path $RepoRoot 'aurserver\app.py'
if (-not (Test-Path -LiteralPath $appModule)) {
    Write-Warning 'aurserver/app.py is not present yet — the API entry point is still being written.'
    Write-Warning "uvicorn will fail to import 'aurserver.app:app' until it lands."
}

# --- build the uvicorn command line -----------------------------------------
$uvicornArgs = @(
    '-m', 'uvicorn',
    'aurserver.app:app',
    '--host', $BindHost,
    '--port', "$Port",
    '--app-dir', $RepoRoot,
    '--log-level', 'info'
)
if (-not $NoReload) {
    $uvicornArgs += '--reload'
}

$url = "http://$BindHost`:$Port/"
Write-Host "  UI     : $url" -ForegroundColor Green
Write-Host "  API    : ${url}api/" -ForegroundColor DarkGray
Write-Host "  reload : $(if ($NoReload) { 'off' } else { 'on' })"
Write-Host '  stop   : Ctrl+C'
Write-Host ''

& $Python.Exe @($Python.Prefix + $uvicornArgs)
exit $LASTEXITCODE
