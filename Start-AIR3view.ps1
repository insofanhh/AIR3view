$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run Setup-AIR3view.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Frontend is not built. Run Setup-AIR3view.ps1 first.' }
$port = if ($env:AIR3VIEW_PORT) { $env:AIR3VIEW_PORT } else { '8000' }
Write-Host "AIR3view: http://127.0.0.1:$port - Press Ctrl+C to stop."
& $pythonPath (Join-Path $PSScriptRoot 'run.py')
exit $LASTEXITCODE
