param([string]$Version = '0.1.0')

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
$build = Join-Path $root 'build\windows'
$stage = Join-Path $build 'app'
$release = Join-Path $build 'release'

function Assert-WorkspacePath([string]$path) {
    $full = [IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path outside repository: $full"
    }
    return $full
}

Assert-WorkspacePath $build | Out-Null
if (Test-Path -LiteralPath $build) { Remove-Item -LiteralPath $build -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage, $release | Out-Null

$hostPython = (Get-Command python -ErrorAction Stop).Source
$pythonVersion = & $hostPython -c 'import platform; print(platform.python_version())'
if ($LASTEXITCODE -ne 0 -or $pythonVersion -notmatch '^3\.12\.\d+$') {
    throw 'Build requires 64-bit Python 3.12 on Windows.'
}
& $hostPython -c 'import struct; assert struct.calcsize("P") == 8'
if ($LASTEXITCODE -ne 0) { throw 'Build requires x64 Python.' }
& $hostPython -c 'import PIL'
if ($LASTEXITCODE -ne 0) { throw 'Install Pillow in the build Python before packaging.' }

npm.cmd --prefix (Join-Path $root 'frontend') ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
npm.cmd --prefix (Join-Path $root 'frontend') run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }

$runtime = Join-Path $stage 'python'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$archive = Join-Path $build 'python-embed.zip'
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip" -OutFile $archive
Expand-Archive -LiteralPath $archive -DestinationPath $runtime
Set-Content -LiteralPath (Join-Path $runtime 'python312._pth') -Encoding ascii -Value @(
    'python312.zip', '.', 'Lib\site-packages', '..', 'import site'
)
$packages = Join-Path $runtime 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $packages | Out-Null
& $hostPython -m pip install --disable-pip-version-check --no-compile --target $packages -r (Join-Path $root 'requirements-runtime.txt') 'pystray==0.19.5'
if ($LASTEXITCODE -ne 0) { throw 'Installing vendored Python packages failed.' }

$backend = Join-Path $stage 'backend'
New-Item -ItemType Directory -Force -Path $backend | Out-Null
Get-ChildItem -LiteralPath (Join-Path $root 'backend') -Filter '*.py' -File |
    Copy-Item -Destination $backend
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'frontend') | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'frontend\dist') -Destination (Join-Path $stage 'frontend\dist') -Recurse
Copy-Item -LiteralPath (Join-Path $root 'run.py') -Destination $stage
Set-Content -LiteralPath (Join-Path $stage 'APP_VERSION') -Value $Version -Encoding ascii
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'launcher.py') -Destination $stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY.md') -Destination $stage

function Find-Executable([string]$name, [string]$packageName) {
    $candidates = @()
    if ($env:ChocolateyInstall) {
        $packageDir = Join-Path $env:ChocolateyInstall "lib\$packageName"
        if (Test-Path -LiteralPath $packageDir) {
            $candidates += Get-ChildItem -LiteralPath $packageDir -Filter $name -File -Recurse -ErrorAction SilentlyContinue
        }
    }
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command -and (Test-Path -LiteralPath $command.Source)) {
        $candidates += Get-Item -LiteralPath $command.Source
    }
    $result = $candidates | Sort-Object Length -Descending | Select-Object -First 1
    if (-not $result -or $result.Length -lt 10000000) {
        throw "A full $name binary is required; install $packageName first."
    }
    return $result.FullName
}

$tools = Join-Path $stage 'tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
Copy-Item -LiteralPath (Find-Executable 'ffmpeg.exe' 'ffmpeg') -Destination (Join-Path $tools 'ffmpeg.exe')
Copy-Item -LiteralPath (Find-Executable 'deno.exe' 'deno') -Destination (Join-Path $tools 'deno.exe')

$codexInstall = Join-Path $build 'codex-npm'
New-Item -ItemType Directory -Force -Path $codexInstall | Out-Null
npm.cmd --prefix $codexInstall install --no-save --ignore-scripts '@openai/codex@latest'
if ($LASTEXITCODE -ne 0) { throw 'Bundling Codex CLI failed.' }
$codex = Get-ChildItem -LiteralPath (Join-Path $codexInstall 'node_modules') -Filter 'codex.exe' -File -Recurse |
    Sort-Object Length -Descending | Select-Object -First 1
if (-not $codex -or $codex.Length -lt 10000000) { throw 'Native Codex CLI binary is missing.' }
Copy-Item -LiteralPath $codex.FullName -Destination (Join-Path $tools 'codex.exe')

$filters = & (Join-Path $tools 'ffmpeg.exe') -hide_banner -filters 2>&1
if ($LASTEXITCODE -ne 0) { throw 'Bundled FFmpeg failed.' }
if (-not ($filters | Select-String '\bass\s')) { throw 'FFmpeg must include libass.' }
& (Join-Path $tools 'ffmpeg.exe') -L | Set-Content -LiteralPath (Join-Path $stage 'FFMPEG-LICENSE.txt') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Could not capture bundled FFmpeg license.' }
& (Join-Path $tools 'ffmpeg.exe') -version | Set-Content -LiteralPath (Join-Path $stage 'FFMPEG-BUILD.txt') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Could not capture bundled FFmpeg build information.' }
& (Join-Path $tools 'deno.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'Bundled Deno failed.' }
& (Join-Path $tools 'codex.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'Bundled Codex CLI failed.' }

& $hostPython (Join-Path $PSScriptRoot 'make_icon.py') (Join-Path $stage 'AIR3view.ico')
if ($LASTEXITCODE -ne 0) { throw 'Generating the application icon failed.' }

$env:AIR3VIEW_DATA = Join-Path $build 'smoke-data'
$env:FFMPEG_PATH = Join-Path $tools 'ffmpeg.exe'
$env:CODEX_PATH = Join-Path $tools 'codex.exe'
$env:PATH = $tools + [IO.Path]::PathSeparator + $env:PATH
& (Join-Path $runtime 'python.exe') (Join-Path $PSScriptRoot 'smoke.py')
if ($LASTEXITCODE -ne 0) {
    $smokeLog = Join-Path $stage 'smoke-server.log'
    if (Test-Path -LiteralPath $smokeLog) { Get-Content -LiteralPath $smokeLog -Tail 60 }
    throw 'Packaged runtime smoke test failed.'
}
Remove-Item -LiteralPath (Join-Path $stage 'smoke-server.log') -ErrorAction SilentlyContinue

$iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
if (-not (Test-Path -LiteralPath $iscc)) { throw 'Inno Setup 6 compiler is required.' }
& $iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot 'AIR3view.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
$installer = Get-ChildItem -LiteralPath $release -Filter 'AIR3view-Setup-*.exe' -File |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw 'No installer was produced.' }
Write-Host "Installer ready: $($installer.FullName)"
