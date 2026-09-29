param([switch]$ValidateOnly)

if ($ValidateOnly) { exit 0 }

$ErrorActionPreference = 'Stop'
$installer = $env:AIR3VIEW_UPDATE_INSTALLER
$root = $env:AIR3VIEW_UPDATE_ROOT
$data = $env:AIR3VIEW_UPDATE_DATA
$version = $env:AIR3VIEW_UPDATE_VERSION
$previousPid = [int]$env:AIR3VIEW_UPDATE_PID
$port = [int]$env:AIR3VIEW_UPDATE_PORT
$log = Join-Path $data 'update-installer.log'
$resultPath = Join-Path $data 'result.json'

function Save-Result([string]$status, [string]$message) {
    $payload = @{status=$status; message=$message; version=$version; updated=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress
    $temporary = $resultPath + '.tmp'
    [IO.File]::WriteAllText($temporary, $payload, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $resultPath -Force
}

function Start-Air3view {
    $pythonw = Join-Path $root 'python\pythonw.exe'
    $launcher = Join-Path $root 'launcher.py'
    if ((Test-Path -LiteralPath $pythonw) -and (Test-Path -LiteralPath $launcher)) {
        Start-Process -FilePath $pythonw -ArgumentList ('"' + $launcher + '"') -WorkingDirectory $root -WindowStyle Hidden
    }
}

try {
    Write-Output "AIR3view update helper started for v$version"
    if (-not (Test-Path -LiteralPath $installer)) { throw 'Không tìm thấy bộ cài đã tải.' }
    if (Get-Process -Id $previousPid -ErrorAction SilentlyContinue) {
        Wait-Process -Id $previousPid -Timeout 90 -ErrorAction Stop
    }
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS', ('/LOG="' + $log + '"'))
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw "Bộ cài dừng với mã $($process.ExitCode)." }
    $installed = (Get-Content -LiteralPath (Join-Path $root 'APP_VERSION') -Raw).Trim()
    if ($installed -ne $version) { throw "Bản cài báo $installed thay vì $version." }
    Start-Air3view
    $healthy = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $reply = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2
            if ($reply.ok -and $reply.version -eq $version) { $healthy = $true; break }
        } catch { }
    }
    if (-not $healthy) { throw 'AIR3view mới chưa khởi động và xác nhận phiên bản sau khi cài.' }
    Save-Result 'success' "Đã cập nhật AIR3view v$version."
} catch {
    $message = "Cập nhật AIR3view v$version thất bại: $($_.Exception.Message) Xem $log"
    try { Save-Result 'failed' $message } catch { }
    if (-not (Get-Process -Id $previousPid -ErrorAction SilentlyContinue)) {
        try { Start-Air3view } catch { }
    }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message, 'AIR3view', 'OK', 'Error') | Out-Null
    exit 1
}
