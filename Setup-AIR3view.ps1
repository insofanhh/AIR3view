param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
foreach ($tool in @('node', 'npm.cmd')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing $tool. Install Node.js and reopen PowerShell." }
}
$ffmpegPath = if ($env:FFMPEG_PATH) { $env:FFMPEG_PATH } else { 'ffmpeg' }
if (-not (Get-Command $ffmpegPath -ErrorAction SilentlyContinue)) { throw 'FFmpeg not found. Add it to PATH or set FFMPEG_PATH.' }
$filters = & $ffmpegPath -hide_banner -filters 2>&1
if ($LASTEXITCODE -ne 0 -or -not ($filters | Select-String '\bass\s')) { throw 'FFmpeg must include the ass/libass subtitle filter.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
}
& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Failed to update pip.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Failed to install Python dependencies.' }
npm.cmd --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw 'Failed to install frontend dependencies.' }
npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Write-Host 'AIR3view installed. Configure OmniVoice and the AI provider as described in README.md.'
Write-Host 'Run Start-AIR3view.ps1 to launch the app.'
