"""Private persistent SDK worker. JSON lines over pipes, no listening port."""
import json
import os
from pathlib import Path
import sys


_TRANSFORMERS_API = (
    'PretrainedConfig', 'PreTrainedModel', 'AutoTokenizer', 'AutoModel',
    'Qwen3Config', 'Qwen3Model',
)


def _gpu_dependency_warning():
    """Validate the isolated GPU stack before selecting the PyTorch backend."""
    try:
        import torch
    except Exception as exc:
        return f'PyTorch không nạp được ({type(exc).__name__}); tự chuyển sang CPU/ONNX.'
    try:
        import transformers
        missing = [name for name in _TRANSFORMERS_API if not hasattr(transformers, name)]
        if missing or not getattr(transformers, '__file__', None):
            names = ', '.join(missing) or 'transformers.__file__'
            return (f'Transformers GPU chưa đầy đủ ({names}); '
                    'tự chuyển sang CPU/ONNX.')
    except Exception as exc:
        return (f'Transformers GPU không nạp được ({type(exc).__name__}: {exc}); '
                'tự chuyển sang CPU/ONNX.')
    try:
        if not torch.cuda.is_available():
            return 'PyTorch chưa nhận CUDA; tự chuyển sang CPU/ONNX.'
    except Exception as exc:
        return f'CUDA không khởi tạo được ({type(exc).__name__}); tự chuyển sang CPU/ONNX.'
    return ''


def select_device(requested):
    """Use the installed CUDA stack when ready; keep TTS usable otherwise."""
    if requested not in ('auto', 'cpu', 'cuda'):
        raise ValueError('Thiết bị VieNeu không hợp lệ.')
    if requested == 'cpu':
        return 'cpu', ''
    warning = _gpu_dependency_warning()
    if warning:
        return 'cpu', warning
    return 'cuda', ''


def _is_gpu_setup_error(exc):
    """Whether a failed GPU constructor can safely be retried with ONNX/CPU."""
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return True
    text = str(exc).lower()
    if isinstance(exc, AttributeError) and ('transformers' in text or 'torch' in text):
        return True
    if isinstance(exc, OSError) and any(token in text for token in (
        'dll load failed', 'cuda', 'cudnn', 'nvrtc', 'nvidia',
    )):
        return True
    if isinstance(exc, RuntimeError) and any(token in text for token in (
        'cuda', 'cudnn', 'nvrtc', 'nvidia driver', 'no kernel image',
    )):
        return True
    cause = getattr(exc, '__cause__', None)
    return cause is not None and _is_gpu_setup_error(cause)


def _construct_vieneu(resolved):
    from .vieneu import MODEL
    from vieneu import Vieneu
    return Vieneu(mode='v3turbo', backbone_repo=MODEL, device=resolved,
                  backend='onnx' if resolved == 'cpu' else 'pytorch',
                  precision='fp32', max_batch_size=4)


def load_model(device):
    from .vieneu import MODEL, SDK_VERSION
    from importlib.metadata import version
    if version('vieneu') != SDK_VERSION:
        raise RuntimeError(f'Cần vieneu=={SDK_VERSION}; chạy lại cài đặt requirements.txt.')
    resolved, warning = select_device(device)
    if resolved == 'cpu':
        from . import store
        from .vieneu_onnx_files import install_sdk_fetch_hook
        install_sdk_fetch_hook(store.DATA)
        return _construct_vieneu('cpu'), 'cpu', warning
    try:
        return _construct_vieneu('cuda'), 'cuda', ''
    except Exception as exc:
        if not _is_gpu_setup_error(exc):
            raise
        # The isolated GPU environment may pass import/readiness checks but
        # fail while loading a native driver. Retry once with torch-free ONNX.
        from . import store
        from .vieneu_onnx_files import install_sdk_fetch_hook
        install_sdk_fetch_hook(store.DATA)
        fallback = _construct_vieneu('cpu')
        detail = f'{type(exc).__name__}: {exc}'
        return (fallback, 'cpu',
                f'VieNeu GPU không khởi tạo được ({detail}); tự chuyển sang CPU/ONNX.')


def synthesize(tts, params, output):
    import numpy as np
    voice = params.get('voice')
    if voice and voice not in {value for _, value in tts.list_preset_voices()}:
        raise ValueError(f'Giọng "{voice}" không có trong VieNeu v3 Turbo. Chọn lại giọng trong AIR3view hoặc dùng audio tham chiếu.')
    audio = tts.infer(**params)
    if audio is None or not np.size(audio) or not np.all(np.isfinite(audio)):
        raise RuntimeError('VieNeu v3 Turbo trả audio rỗng hoặc không hợp lệ.')
    tts.save(audio, str(output))


def main():
    # Reserve protocol stdout; redirect even native SDK stdout to the log.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    def send(value):
        protocol.write(json.dumps(value, ensure_ascii=False) + '\n')
        protocol.flush()
    tts = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if tts is None:
                tts, device, warning = load_model(sys.argv[1])
            send({'ready': {'device': device, 'warning': warning,
                            'voices': [{'label': label, 'id': value}
                                       for label, value in tts.list_preset_voices()]}})
            synthesize(tts, request['params'], Path(request['output']))
            send({'done': True})
        except Exception as exc:
            import traceback
            traceback.print_exc(file=sys.stderr)
            send({'error': f'VieNeu SDK: {type(exc).__name__}: {exc}'})


if __name__ == '__main__':
    main()
