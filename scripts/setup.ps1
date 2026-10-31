# Compatible with Windows PowerShell 5.1 (included in Windows 11).
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

try {
    $venvPython = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        if (Test-Path '.venv') {
            throw 'The .venv directory is not a Windows virtual environment. Rename or remove only .venv, then run setup.cmd again. Keep the data directory.'
        }
        $pythonCommand = $null
        $pythonArguments = @()
        foreach ($candidate in @(
            @{ Command = 'py'; Arguments = @('-3.12') },
            @{ Command = 'py'; Arguments = @('-3') },
            @{ Command = 'python'; Arguments = @() }
        )) {
            if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) { continue }
            $candidateArgs = $candidate.Arguments
            try {
                & $candidate.Command @candidateArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>$null
            } catch { continue }
            if ($LASTEXITCODE -eq 0) {
                $pythonCommand = $candidate.Command
                $pythonArguments = $candidateArgs
                break
            }
        }
        if (-not $pythonCommand) {
            throw 'Python 3.12 or newer was not found. Install 64-bit Python from python.org, include the Python launcher and Add python.exe to PATH, then run setup.cmd again.'
        }
        & $pythonCommand @pythonArguments -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
    }
    & $venvPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'The existing .venv needs Python 3.12+. Rename .venv and run setup again. Keep data.' }
    & $venvPython -m pip install -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Read the pip error above, then rerun setup.cmd.' }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    Write-Host 'Setup complete. Double-click start.cmd to run Movie Harbor.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host ('Setup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
