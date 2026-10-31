# Use a dedicated variable: $Host is a built-in read-only PowerShell variable.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

try {
    $venvPython = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) { throw 'Run setup.cmd first to install Movie Harbor.' }
    $bindAddress = '127.0.0.1'
    if ($env:HOST) { $bindAddress = $env:HOST }
    $listenPort = 8000
    if ($env:PORT) {
        if (-not [int]::TryParse($env:PORT, [ref]$listenPort) -or $listenPort -lt 1 -or $listenPort -gt 65535) {
            throw 'PORT must be a number between 1 and 65535.'
        }
    }
    if ($bindAddress -notin @('127.0.0.1', 'localhost', '::1') -and -not $env:APP_PASSWORD) {
        throw 'Set APP_PASSWORD before exposing Movie Harbor on the LAN. Login username: admin.'
    }
    Write-Host "Starting Movie Harbor on port $listenPort. Leave this window open; press Ctrl+C to stop."
    Write-Host "On this PC, open your browser at http://localhost:$listenPort"
    & $venvPython -m uvicorn app.main:app --host $bindAddress --port $listenPort --workers 1
    if ($LASTEXITCODE -ne 0) { throw 'The server stopped with an error. Read the message above (the port may already be in use).' }
    exit 0
} catch {
    Write-Host ('Startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
