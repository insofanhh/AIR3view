"""Local VieNeu v3 Turbo SDK, isolated from the web server's native libraries."""
import atexit
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

SDK_VERSION = '3.7.1'
MODEL = 'pnnbao-ump/VieNeu-TTS-v3-Turbo'
DEFAULT_VOICE = 'Minh Quân'
TEMPERATURE = .8


def installation():
    try:
        return importlib.metadata.distribution('vieneu')
    except importlib.metadata.PackageNotFoundError:
        return None


def status():
    """Cheap polling: never import the SDK or download/load a model here."""
    dist = installation()
    ok = dist is not None and dist.version == SDK_VERSION
    voices = []
    if ok:
        try:
            data = json.loads(Path(dist.locate_file('vieneu/assets/voices_v3_turbo.json')).read_text(encoding='utf-8'))
            voices = [{'id': name, 'label': name + (' — ' + v['description'] if v.get('description') else '')}
                      for name, v in data.get('presets', {}).items()]
        except (OSError, ValueError):
            pass
    loaded = _runtime.process is not None and _runtime.process.poll() is None and bool(_runtime.info)
    return {'ok': ok, 'provider': 'vieneu', 'mode': 'sdk', 'version': dist.version if dist else None,
            'model': MODEL, 'loaded': loaded, 'device': _runtime.info.get('device') if loaded else None,
            'voices': _runtime.info.get('voices', voices) if loaded else voices,
            'message': ('Model đã nạp · ' + str(_runtime.info.get('device')) +
                        (' · ' + _runtime.info['warning'] if _runtime.info.get('warning') else '') if loaded else
                        'SDK đã cài · model tự tải/nạp khi tạo giọng lần đầu.') if ok else
                       'Cần cài VieNeu SDK: .venv\\Scripts\\python.exe -m pip install -r requirements.txt'}


def infer_parameters(settings, text, reference=None):
    if settings.get('language', 'Vietnamese') not in ('Vietnamese', 'English'):
        raise ValueError('VieNeu v3 Turbo hỗ trợ tiếng Việt và English. Chọn OmniVoice cho ngôn ngữ khác.')
    if not text.strip():
        raise ValueError('Lời đọc VieNeu đang trống.')
    params = dict(text=text, temperature=TEMPERATURE, max_chars=256)
    if settings['voice_mode'] == 'clone':
        if reference is None or not Path(reference).is_file():
            raise ValueError('Chọn audio giọng tham chiếu trước khi tạo giọng VieNeu.')
        params.update(ref_audio=str(Path(reference).resolve()), denoise=True)
    else:
        params['voice'] = settings.get('vieneu_voice', '').strip() or DEFAULT_VOICE
    return params


def reference_clip(project, report, check):
    """v3 Turbo encodes the speaker directly; no reference transcript/ASR."""
    from . import store, providers
    from .media import run, FFMPEG
    import hashlib
    source = store.asset(project['id'], project['settings']['voice_reference'])
    if not source.is_file():
        raise ValueError('Không tìm thấy audio giọng mẫu cho VieNeu.')
    check()
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            check()
            digest.update(block)
    reference_hash = digest.hexdigest()
    if project['settings'].get('voice_reference_hash') != reference_hash:
        project['settings']['voice_reference_hash'] = reference_hash
        store.save(project)
    fingerprint = providers.digest({'reference': reference_hash, 'v3_clip': 1})
    folder = store.project_dir(project['id']) / 'vieneu-references'
    folder.mkdir(exist_ok=True)
    audio = folder / (fingerprint + '.wav')
    if not audio.exists():
        report(2, 'Chuẩn bị giọng mẫu VieNeu v3 Turbo…')
        temporary = audio.with_suffix('.tmp.wav')
        run([FFMPEG, '-y', '-i', source, '-t', '7.5', '-ar', '48000', '-ac', '1', temporary], check_cancel=check)
        check()
        temporary.replace(audio)
    return audio


def worker_environment(data_dir):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    env.setdefault('HF_HOME', str(data_dir / 'models' / 'huggingface'))
    # The GPU backbone needs transformers with huggingface-hub<1, while
    # AIR3view's Gradio client uses a newer hub. Isolate only this worker.
    gpu_packages = data_dir / 'vieneu-gpu-python'
    if gpu_packages.is_dir():
        env['PYTHONPATH'] = str(gpu_packages) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


class LocalRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.messages = queue.Queue()
        self.device = None
        self.info = {}
        self.log = None

    def close(self):
        process, self.process = self.process, None
        self.info = {}
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()
        if self.log:
            self.log.close()
            self.log = None

    @staticmethod
    def _read(pipe, messages):
        try:
            for line in pipe:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            messages.put({'error': 'Tiến trình VieNeu đã dừng. Xem data/vieneu-sdk.log; thử lại để nạp lại model.'})

    def _start(self, device):
        from . import store
        self.close()
        state = status()
        if not state['ok']:
            raise RuntimeError(state['message'])
        self.device = device
        self.messages = queue.Queue()
        env = worker_environment(store.DATA)
        self.log = (store.DATA / 'vieneu-sdk.log').open('a', encoding='utf-8')
        self.process = subprocess.Popen([sys.executable, '-u', '-m', 'backend.vieneu_worker', device],
                                        cwd=store.ROOT, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log,
                                        text=True, encoding='utf-8',
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, args=(self.process.stdout, self.messages), daemon=True).start()

    def generate(self, params, destination, device, report, check, progress=5, timeout=1200):
        check()
        while not self.lock.acquire(timeout=.2):
            check()
        temporary = destination.with_suffix('.tmp.wav')
        try:
            check()
            if self.process is None or self.process.poll() is not None or self.device != device:
                self._start(device)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.process.stdin.write(json.dumps({'params': params, 'output': str(temporary.resolve())}, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
            start = last_report = time.monotonic()
            phase = ('VieNeu v3 Turbo tạo giọng · ' + self.info['device'] if self.info else
                     'Tải/nạp model VieNeu v3 Turbo (lần đầu cần Internet)')
            report(progress, phase + '…')
            while True:
                check()
                elapsed = time.monotonic() - start
                if elapsed > timeout:
                    actual = self.info.get('device', device) if self.info else device
                    raise TimeoutError(
                        f'VieNeu SDK quá {round(timeout / 60, 1):g} phút trên {actual}. '
                        'Kiểm tra bộ CUDA/Transformers và quyền đọc model, hoặc chạy lại bằng thiết bị phù hợp.'
                    )
                try:
                    message = self.messages.get(timeout=.2)
                except queue.Empty:
                    if time.monotonic() - last_report >= 10:
                        report(progress, f'{phase} · {round(elapsed)}s')
                        last_report = time.monotonic()
                    continue
                if 'error' in message:
                    raise RuntimeError(message['error'])
                if 'ready' in message:
                    self.info = message['ready']
                    phase = 'VieNeu v3 Turbo tạo giọng · ' + self.info['device']
                    report(progress, phase + (' · ' + self.info['warning'] if self.info.get('warning') else '') + '…')
                if message.get('done'):
                    check()
                    from .media import probe
                    if not temporary.is_file() or probe(temporary)['duration'] <= 0:
                        raise RuntimeError('VieNeu SDK trả audio rỗng hoặc không hợp lệ.')
                    temporary.replace(destination)
                    return destination
        except BaseException:
            # A native infer cannot be interrupted by a Python cancellation flag.
            self.close()
            raise
        finally:
            temporary.unlink(missing_ok=True)
            self.lock.release()


_runtime = LocalRuntime()
atexit.register(_runtime.close)


def generate(settings, text, destination, report, check, reference=None, progress=5,
             target_duration=0):
    """Create raw audio with a bounded per-segment watchdog.

    A broken CUDA install can fall back to ONNX/CPU. That fallback is useful,
    but allowing one native call to occupy the worker for 20 minutes makes a
    batch appear frozen. A planned segment has enough information for a
    proportional deadline; unplanned calls keep a conservative ten-minute
    limit for either backend.
    """
    params = infer_parameters(settings, text, reference)
    requested = settings.get('vieneu_device', 'cpu')
    # The requested device may fall back to CPU after the worker loads the
    # model. Use the same bounded deadline for both paths so a broken CUDA
    # environment cannot silently restore the old 20-minute wait.
    timeout = 600
    if target_duration:
        timeout = min(timeout, max(120, int(float(target_duration) * 20)))
    return _runtime.generate(params, Path(destination), requested, report, check, progress,
                             timeout=timeout)
