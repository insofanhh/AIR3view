$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Chạy Setup-AIR3view.ps1 trước.' }

& $python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'Không cài được PyTorch CUDA.' }

$packages = Join-Path $PSScriptRoot 'data\vieneu-gpu-python'
$staging = "$packages.staging-$PID"
if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $staging | Out-Null
& $python -m pip install --target $staging --upgrade --no-deps transformers==4.57.6 huggingface-hub==0.36.2
if ($LASTEXITCODE -ne 0) { throw 'Không cài được thư viện riêng cho VieNeu GPU.' }

# Do not publish a partially copied package. Verify the exact API used by the
# VieNeu PyTorch backend before atomically replacing the previous stack.
$oldPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $staging + $(if ($oldPythonPath) { ";$oldPythonPath" } else { '' })
& $python -c 'import transformers; from transformers import PretrainedConfig, PreTrainedModel, AutoTokenizer, AutoModel, Qwen3Config, Qwen3Model; print("Transformers GPU API: OK", transformers.__version__)'
$transformersExit = $LASTEXITCODE
$env:PYTHONPATH = $oldPythonPath
if ($transformersExit -ne 0) {
    Remove-Item -LiteralPath $staging -Recurse -Force
    throw 'Bộ transformers GPU không đầy đủ (thiếu API VieNeu cần).'
}

$backup = "$packages.backup"
if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
if (Test-Path -LiteralPath $packages) { Move-Item -LiteralPath $packages -Destination $backup }
Move-Item -LiteralPath $staging -Destination $packages
if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }

# The installer may be run elevated while AIR3view later runs as the normal
# desktop user.  Grant that same user read/execute access so Python does not
# see an unreadable ``transformers`` directory as an empty namespace package.
$desktopUser = "$($env:USERDOMAIN)\$($env:USERNAME)"
& icacls $packages /grant "${desktopUser}:(OI)(CI)(RX)" /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Không cấp được quyền đọc bộ thư viện VieNeu GPU cho người dùng hiện tại.' }

& $python -c 'import sys, torch; ok=torch.cuda.is_available(); print("CUDA:", ok, "| GPU:", torch.cuda.get_device_name(0) if ok else "none"); sys.exit(0 if ok else 1)'
if ($LASTEXITCODE -ne 0) { throw 'PyTorch CUDA không khởi tạo được.' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Các gói Python chính của AIR3view đang xung đột.' }
Write-Host 'Đã cài VieNeu CUDA. Khởi động lại AIR3view rồi chọn NVIDIA CUDA trong mục Giọng.'
