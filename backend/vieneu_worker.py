"""Private persistent SDK worker. JSON lines over pipes, no listening port."""
import json
import os
from pathlib import Path
import sys


def select_device(requested):
    """Use the installed CUDA stack when ready; keep TTS usable otherwise."""
    if requested not in ('auto', 'cpu', 'cuda'):
        raise ValueError('Thiết bị VieNeu không hợp lệ.')
    if requested == 'cpu':
        return 'cpu', ''
    try:
        import torch
        import transformers  # noqa: F401 - required by the PyTorch backbone
    except ImportError:
        return 'cpu', 'CUDA thiếu PyTorch hoặc transformers; tự chuyển sang CPU/ONNX.'
    if not torch.cuda.is_available():
        return 'cpu', 'PyTorch chưa nhận CUDA; tự chuyển sang CPU/ONNX.'
    return 'cuda', ''


def load_model(device):
    from .vieneu import MODEL, SDK_VERSION
    from importlib.metadata import version
    if version('vieneu') != SDK_VERSION:
        raise RuntimeError(f'Cần vieneu=={SDK_VERSION}; chạy lại cài đặt requirements.txt.')
    resolved, warning = select_device(device)
    from vieneu import Vieneu
    tts = Vieneu(mode='v3turbo', backbone_repo=MODEL, device=resolved,
                 backend='onnx' if resolved == 'cpu' else 'pytorch', precision='fp32', max_batch_size=4)
    return tts, resolved, warning


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
