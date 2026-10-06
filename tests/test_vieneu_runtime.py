import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from backend import media, store, vieneu
from backend.models import Settings
from backend.vieneu_worker import select_device, synthesize


def test_worker_rejects_unknown_preset_without_changing_speaker(tmp_path):
    tts = SimpleNamespace(list_preset_voices=lambda: [('Minh Quân', 'Minh Quân')],
                          infer=lambda **kw: pytest.fail('must not generate with another speaker'))
    with pytest.raises(ValueError, match='Chọn lại giọng'):
        synthesize(tts, {'voice': 'Old Studio voice'}, tmp_path/'out.wav')


def test_cuda_without_pytorch_falls_back_to_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, 'torch', None)
    monkeypatch.setitem(sys.modules, 'transformers', None)
    device, warning = select_device('cuda')
    assert device == 'cpu' and 'PyTorch' in warning
    assert select_device('auto')[0] == 'cpu'
    assert select_device('cpu') == ('cpu', '')


def test_cuda_ready_uses_gpu_and_unavailable_driver_uses_cpu(monkeypatch):
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    transformers = SimpleNamespace(
        __file__='transformers/__init__.py',
        PretrainedConfig=object, PreTrainedModel=object,
        AutoTokenizer=object, AutoModel=object,
        Qwen3Config=object, Qwen3Model=object,
    )
    monkeypatch.setitem(sys.modules, 'torch', torch)
    monkeypatch.setitem(sys.modules, 'transformers', transformers)
    assert select_device('cuda') == ('cuda', '')
    torch.cuda.is_available = lambda: False
    device, warning = select_device('cuda')
    assert device == 'cpu' and 'CUDA' in warning


def test_incomplete_transformers_namespace_falls_back_to_cpu(monkeypatch):
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, 'torch', torch)
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(__file__=None))
    device, warning = select_device('cuda')
    assert device == 'cpu'
    assert 'Transformers' in warning


def test_gpu_constructor_dependency_error_retries_cpu(monkeypatch, tmp_path):
    from backend import vieneu_worker
    monkeypatch.setattr(vieneu_worker, 'select_device', lambda _: ('cuda', ''))
    monkeypatch.setattr(vieneu_worker, '_construct_vieneu',
                        lambda device: (_ for _ in ()).throw(ImportError('PretrainedConfig'))
                        if device == 'cuda' else 'cpu-tts')
    monkeypatch.setattr('backend.store.DATA', tmp_path)
    installed = []
    monkeypatch.setattr('backend.vieneu_onnx_files.install_sdk_fetch_hook',
                        lambda data: installed.append(data))
    tts, device, warning = vieneu_worker.load_model('cuda')
    assert (tts, device) == ('cpu-tts', 'cpu')
    assert installed == [tmp_path]
    assert 'CPU/ONNX' in warning


def test_gpu_worker_uses_isolated_transformers_before_app_packages(tmp_path, monkeypatch):
    packages = tmp_path/'vieneu-gpu-python'
    packages.mkdir()
    monkeypatch.setenv('PYTHONPATH', 'existing-packages')
    monkeypatch.delenv('HF_HOME', raising=False)
    env = vieneu.worker_environment(tmp_path)
    assert env['PYTHONPATH'].split(os.pathsep) == [str(packages), 'existing-packages']
    assert env['HF_HOME'] == str(tmp_path/'models'/'huggingface')


@pytest.mark.parametrize('audio', [None, np.array([]), np.array([np.nan]), np.array([np.inf])])
def test_worker_never_saves_invalid_waveform(tmp_path, audio):
    tts = SimpleNamespace(infer=lambda **kw: audio, save=lambda *a: pytest.fail('invalid audio'))
    with pytest.raises(RuntimeError, match='audio rỗng'):
        synthesize(tts, {'text': 'Hi', 'ref_audio': 'ref.wav'}, tmp_path/'out.wav')


def test_status_does_not_import_sdk_or_call_network(monkeypatch, tmp_path):
    from backend.app import tts_status
    monkeypatch.setitem(sys.modules, 'vieneu', None)
    monkeypatch.setattr('requests.get', lambda *a, **kw: pytest.fail('no port 7860 request'))
    monkeypatch.setattr(vieneu, 'installation', lambda: None)
    result = tts_status('vieneu', 'http://localhost:7860')
    assert not result['ok'] and result['mode'] == 'sdk' and not result['loaded']
    assert 'requirements.txt' in result['message']


def test_status_lists_bundled_voices_without_loading_model(monkeypatch, tmp_path):
    asset = tmp_path/'voices.json'
    asset.write_text(json.dumps({'presets': {'Test voice': {'description': 'Test'}}}))
    monkeypatch.setattr(vieneu, 'installation', lambda: SimpleNamespace(
        version=vieneu.SDK_VERSION, locate_file=lambda p: asset))
    state = vieneu.status()
    assert state['ok'] and state['voices'] == [{'id': 'Test voice', 'label': 'Test voice — Test'}]


def test_status_reports_actual_cpu_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(vieneu, 'installation', lambda: SimpleNamespace(
        version=vieneu.SDK_VERSION, locate_file=lambda _: tmp_path/'missing.json'))
    monkeypatch.setattr(vieneu._runtime, 'process', SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(vieneu._runtime, 'info', {'device': 'cpu', 'warning': 'CUDA unavailable', 'voices': []})
    state = vieneu.status()
    assert state['device'] == 'cpu' and 'CUDA unavailable' in state['message']


class FakeProcess:
    def __init__(self, runtime):
        self.runtime = runtime
        self.stdin = self
        self.stdout = io.StringIO()
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def write(self, line):
        request = json.loads(line)
        Path(request['output']).write_bytes(b'audio')
        self.runtime.messages.put({'ready': {'device': 'cpu', 'voices': []}})
        self.runtime.messages.put({'done': True})

    def flush(self):
        pass

    def terminate(self):
        self.terminated = True

    def wait(self, timeout):
        return 0

    def close(self):
        pass


def runtime_fixture(monkeypatch):
    runtime = vieneu.LocalRuntime()
    starts = []
    def start(device):
        starts.append(device)
        runtime.device = device
        runtime.process = FakeProcess(runtime)
    monkeypatch.setattr(runtime, '_start', start)
    monkeypatch.setattr(media, 'probe', lambda p: {'duration': 3})
    return runtime, starts


def test_model_process_reused_and_audio_published_atomically(monkeypatch, tmp_path):
    runtime, starts = runtime_fixture(monkeypatch)
    try:
        for name in ('one', 'two'):
            output = runtime.generate({}, tmp_path/(name+'.wav'), 'cpu', lambda *a: None, lambda: None)
            assert output.read_bytes() == b'audio'
            assert not output.with_suffix('.tmp.wav').exists()
        assert starts == ['cpu']
    finally:
        runtime.close()


@pytest.mark.parametrize('failure', ['cancel', 'timeout', 'crash'])
def test_failed_worker_stops_without_publishing_partial_audio(monkeypatch, tmp_path, failure):
    runtime, starts = runtime_fixture(monkeypatch)
    runtime._start('cpu')
    process = runtime.process
    def report(*args):
        if failure == 'cancel':
            raise media.Cancelled('cancelled')
        if failure == 'crash':
            while not runtime.messages.empty():
                runtime.messages.get()
            runtime.messages.put({'error': 'worker crashed'})
    output = tmp_path/'output.wav'
    output.write_bytes(b'previous good result')
    with pytest.raises((media.Cancelled, TimeoutError, RuntimeError)):
        runtime.generate({}, output, 'cpu', report, lambda: None, timeout=-1 if failure == 'timeout' else 5)
    assert process.terminated and runtime.process is None
    assert output.read_bytes() == b'previous good result'
    assert not output.with_suffix('.tmp.wav').exists()
    assert runtime.lock.acquire(blocking=False)
    runtime.lock.release()


def test_reference_no_asr_and_content_change_invalidates_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'test.sqlite3')
    store.init()
    project = store.create('Test', {'kind': 'upload', 'file': 'source.mp4'})
    project['settings'].update(voice_mode='clone', voice_reference='ref.wav')
    ref = store.asset(project['id'], 'ref.wav')
    ref.write_bytes(b'first')
    calls = []
    def convert(args, **kw):
        calls.append(args)
        Path(args[-1]).write_bytes(b'converted')
    monkeypatch.setattr(media, 'run', convert)
    monkeypatch.setattr('backend.reference_voice.ensure_transcript', lambda *a: pytest.fail('no ASR for v3'))
    first = vieneu.reference_clip(project, lambda *a: None, lambda: None)
    fingerprint = project['settings']['voice_reference_hash']
    assert vieneu.reference_clip(project, lambda *a: None, lambda: None) == first
    assert len(calls) == 1 and '7.5' in calls[0]
    ref.write_bytes(b'second')
    assert vieneu.reference_clip(project, lambda *a: None, lambda: None) != first
    assert project['settings']['voice_reference_hash'] != fingerprint
