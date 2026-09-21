from types import SimpleNamespace
import pytest
from backend import media


@pytest.mark.parametrize('stage', ['load', 'iteration'])
def test_missing_cuda_restarts_full_audio_on_cpu_and_reuses_it(monkeypatch, stage):
    import faster_whisper
    calls = []
    monkeypatch.setattr(media, '_asr_cache', {})

    class Model:
        def __init__(self, name, device, compute_type, **kwargs):
            calls.append((device, compute_type))
            self.device = device
            if device == 'cuda' and stage == 'load':
                raise RuntimeError('Library cublas64_12.dll is not found or cannot be loaded')

        def transcribe(self, *args, **kwargs):
            def segments():
                if self.device == 'cuda':
                    yield SimpleNamespace(start=0, end=1, text='partial GPU result', words=[])
                    raise RuntimeError('Library cudnn_ops64_9.dll cannot be loaded')
                yield SimpleNamespace(start=0, end=1, text='CPU result', words=[])
            return segments(), None

    monkeypatch.setattr(faster_whisper, 'WhisperModel', Model)
    settings = {'asr_model':'small', 'asr_device':'cuda', 'language':'English'}
    result = media.transcribe('sample.wav', settings)
    assert [c['text'] for c in result] == ['CPU result']
    assert settings['asr_device'] == 'cpu'
    media.transcribe('next.wav', settings)
    assert calls == [('cuda','float16'), ('cpu','int8')]


@pytest.mark.parametrize('error', [RuntimeError('Corrupt model file'),
                                  OSError('Audio file not found'),
                                  media.Cancelled('Cancelled')])
def test_non_cuda_errors_and_cancellation_are_not_hidden(monkeypatch, error):
    calls = []
    def fail(*args):
        calls.append(1)
        raise error
    monkeypatch.setattr(media, '_transcribe', fail)
    settings = {'asr_device':'cuda'}
    with pytest.raises(type(error), match=str(error)):
        media.transcribe('sample.wav', settings)
    assert len(calls) == 1
    assert settings['asr_device'] == 'cuda'


def test_failed_cpu_retry_is_not_reported_as_success(monkeypatch):
    calls = []
    def fail(path, settings, *args):
        calls.append(settings['asr_device'])
        if settings['asr_device'] == 'cuda':
            raise RuntimeError('CUDA failed with error out of memory')
        raise RuntimeError('CPU inference failed')
    monkeypatch.setattr(media, '_asr_cache', {})
    monkeypatch.setattr(media, '_transcribe', fail)
    settings = {'asr_device':'cuda'}
    with pytest.raises(RuntimeError, match='CPU inference failed'):
        media.transcribe('sample.wav', settings)
    assert calls == ['cuda','cpu']
    assert settings['asr_device'] == 'cuda'
