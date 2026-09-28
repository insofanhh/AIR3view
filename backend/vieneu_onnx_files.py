"""Give ONNX Runtime real, adjacent graph and external-data files.

VieNeu 3.7.1 fetches each graph and its external data independently. Hugging Face
snapshots may link them to different blob directories, which ONNX Runtime rejects.
The hook is installed only inside the private VieNeu CPU worker.
"""

import hashlib
import os
from pathlib import Path
import shutil
import uuid


OPTIONAL_FILES = frozenset({'codec_browser_onnx_meta.json'})


def _cached_or_download(repo: str, filename: str, subfolder: str | None) -> Path:
    from huggingface_hub import hf_hub_download
    options = {'repo_id': repo, 'filename': filename, 'repo_type': 'model',
               'subfolder': subfolder or None}
    try:
        return Path(hf_hub_download(**options, local_files_only=True))
    except Exception:
        return Path(hf_hub_download(**options))


def materialized_fetch(repo: str, files: list[str], subfolder: str | None, data_dir: Path) -> Path:
    """Re-use downloaded blobs, copying each missing file atomically to a real dir."""
    from .vieneu import SDK_VERSION

    key = hashlib.sha256(f'{SDK_VERSION}|{repo}|{subfolder or ""}'.encode()).hexdigest()[:20]
    target = data_dir / 'models' / 'vieneu-onnx' / key
    target.mkdir(parents=True, exist_ok=True)
    for name in files:
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Invalid VieNeu model filename')
        output = target / name
        if output.is_file() and not output.is_symlink() and output.stat().st_size > 0:
            continue
        try:
            source = _cached_or_download(repo, name, subfolder)
        except Exception:
            if name in OPTIONAL_FILES:
                continue
            raise
        if not source.is_file() or source.stat().st_size <= 0:
            raise RuntimeError(f'Tệp model VieNeu chưa tải xong: {name}')
        temporary = target / f'.{name}.{uuid.uuid4().hex}.tmp'
        try:
            with source.open('rb') as read, temporary.open('xb') as write:
                shutil.copyfileobj(read, write, length=4 * 1024 * 1024)
                write.flush()
                os.fsync(write.fileno())
            if temporary.stat().st_size != source.stat().st_size:
                raise RuntimeError(f'Tệp model VieNeu sao chép chưa đủ: {name}')
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def install_sdk_fetch_hook(data_dir: Path) -> None:
    """Override the two internal CPU fetches in the pinned VieNeu SDK only."""
    from vieneu._v3_turbo_engine.onnx_runtime_lite import OnnxV3LiteEngine

    OnnxV3LiteEngine._fetch = staticmethod(
        lambda repo, files, subfolder: materialized_fetch(repo, files, subfolder, data_dir))
