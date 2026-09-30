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
$dataRoot = if ($env:AIR3VIEW_DATA) { $env:AIR3VIEW_DATA } else { Join-Path $PSScriptRoot 'data' }
$runtimeDir = Join-Path $dataRoot '_runtime'
$localDeno = Join-Path $runtimeDir 'deno.exe'
$nodeMajor = [int]((& node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 22 -and -not (Get-Command deno -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $localDeno)) {
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    $archive = Join-Path $runtimeDir 'deno.zip'
    try {
        Invoke-WebRequest -Uri 'https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-pc-windows-msvc.zip' -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $runtimeDir -Force
    } finally {
        Remove-Item -LiteralPath $archive -ErrorAction SilentlyContinue
    }
}
if (Test-Path -LiteralPath $localDeno) {
    & $localDeno --version | Select-Object -First 1
    if ($LASTEXITCODE -ne 0) { throw 'Deno installed for YouTube is not working.' }
}
npm.cmd --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw 'Failed to install frontend dependencies.' }
npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Write-Host 'AIR3view installed with VieNeu v3 Turbo SDK. Configure the AI provider; OmniVoice is optional. See README.md.'
Write-Host 'Run Start-AIR3view.ps1 to launch the app.'
