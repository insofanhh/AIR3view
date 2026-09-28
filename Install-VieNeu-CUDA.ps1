$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Chạy Setup-AIR3view.ps1 trước.' }

& $python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'Không cài được PyTorch CUDA.' }

$packages = Join-Path $PSScriptRoot 'data\vieneu-gpu-python'
New-Item -ItemType Directory -Force -Path $packages | Out-Null
& $python -m pip install --target $packages --upgrade --no-deps transformers==4.57.6 huggingface-hub==0.36.2
if ($LASTEXITCODE -ne 0) { throw 'Không cài được thư viện riêng cho VieNeu GPU.' }

& $python -c 'import sys, torch; ok=torch.cuda.is_available(); print("CUDA:", ok, "| GPU:", torch.cuda.get_device_name(0) if ok else "none"); sys.exit(0 if ok else 1)'
if ($LASTEXITCODE -ne 0) { throw 'PyTorch CUDA không khởi tạo được.' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Các gói Python chính của AIR3view đang xung đột.' }
Write-Host 'Đã cài VieNeu CUDA. Khởi động lại AIR3view rồi chọn NVIDIA CUDA trong mục Giọng.'
