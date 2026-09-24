from pathlib import Path

import pytest

from backend import providers, store, vieneu
from backend.models import Settings


class FakeClient:
    def view_api(self, **_kwargs):
        return {'named_endpoints': {endpoint: {'parameters': [
            {'parameter_name': name} for name in
            ('param_0', 'param_1', 'param_2', 'param_3', 'param_5',
             'param_6', 'param_7', 'param_8', 'param_9', 'param_10')
        ]} for endpoint in ('/wrapper', '/wrapper_1')}}

    def predict(self, **_kwargs):
        return '✅ Model đã sẵn sàng'


def test_vieneu_wrapper_uses_reference_and_exact_endpoint(monkeypatch, tmp_path):
    reference = tmp_path / 'reference.wav'
    reference.write_bytes(b'audio')
    monkeypatch.setattr('gradio_client.handle_file', lambda path: {'path': path})
    settings = Settings(tts_provider='vieneu', voice_mode='clone', language='Vietnamese').model_dump()
    params = vieneu.parameters(FakeClient(), settings, 'Xin chào.', reference, 'Xin chao.')
    assert vieneu.synthesis_endpoint(settings) == '/wrapper_1'
    assert params['param_0'] == 'Xin chào.'
    assert params['param_2'] == {'path': str(reference)}
    assert params['param_3'] == 'Xin chao.'
    assert params['param_5'] == 'Standard (Một lần)'
    assert params['param_6'] is False


def test_vieneu_requires_voice_when_no_reference():
    settings = Settings(tts_provider='vieneu', voice_mode='design', vieneu_voice='').model_dump()
    with pytest.raises(ValueError, match='tên giọng|audio giọng'):
        vieneu.parameters(FakeClient(), settings, 'Xin chào.')


def test_preset_uses_story_endpoint_and_explicit_voice():
    settings = Settings(voice_mode='design', vieneu_voice='Minh Quân Pro').model_dump()
    assert vieneu.synthesis_endpoint(settings) == '/wrapper'
    params = vieneu.parameters(FakeClient(), settings, 'Xin chào.')
    assert params['param_1'] == 'Minh Quân Pro'
    assert params['param_2'] is None


def test_clone_never_falls_back_to_preset_endpoint(tmp_path):
    class PresetOnly(FakeClient):
        def view_api(self, **kwargs):
            api = super().view_api(**kwargs)
            del api['named_endpoints']['/wrapper_1']
            return api
    settings = Settings(voice_mode='clone').model_dump()
    with pytest.raises(ValueError, match='/wrapper_1'):
        vieneu.parameters(PresetOnly(), settings, 'Hello', tmp_path/'ref.wav', 'Reference')


def test_vieneu_readiness_explains_unloaded_model():
    class Client(FakeClient):
        def predict(self, **_kwargs):
            return '⏳ Chưa tải model.'
    with pytest.raises(RuntimeError, match='chọn model và bấm Load model'):
        vieneu.ensure_ready(Client())


def test_vieneu_voice_hash_is_separate_from_omnivoice():
    n = {'text': 'A short narration.'}
    base = Settings(tts_provider='vieneu', vieneu_voice='Voice A').model_dump()
    other = {**base, 'tts_provider': 'omnivoice'}
    assert providers.voice_hash(n, base) != providers.voice_hash(n, other)


@pytest.mark.parametrize('seconds', [7.68, 13.8])
def test_natural_duration_is_fitted_without_cutting_or_padding(tmp_path, monkeypatch, seconds):
    import shutil
    from backend import media
    if not shutil.which(media.FFMPEG):
        pytest.skip('FFmpeg required')
    raw = tmp_path/'raw.wav'
    out = tmp_path/'out.wav'
    media.run([media.FFMPEG, '-y', '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}', raw])
    monkeypatch.setattr(providers, '_request_voice_audio', lambda *a: raw)
    providers.generate_voice_audio(None, {}, '/wrapper_1', out, lambda *a: None, lambda: None,
                                   0, 'Test', target_duration=10.393, engine='VieNeu')
    assert abs(media.probe(out)['duration'] - 10.393) < .04


def test_duration_outlier_retries_then_keeps_valid_result(tmp_path, monkeypatch):
    from backend import media
    calls = []
    raw = tmp_path/'raw.wav'
    def request(*args):
        calls.append(1)
        return raw
    monkeypatch.setattr(providers, '_request_voice_audio', request)
    monkeypatch.setattr(providers, 'probe', lambda p: {'duration': 2 if len(calls)==1 else 10})
    monkeypatch.setattr(media, 'run', lambda args, **kw: Path(args[-1]).write_bytes(b'valid'))
    out = tmp_path/'out.wav'
    with pytest.raises(providers.DurationMismatchError):
        providers.generate_voice_audio(None, {}, '/wrapper_1', out, lambda *a: None, lambda: None,
                                       0, 'Test', target_duration=10, engine='VieNeu')
    assert len(calls)==1 and not out.exists()


def test_duration_outlier_stops_after_three_attempts(tmp_path, monkeypatch):
    calls = []
    def request(*args):
        calls.append(1)
        return tmp_path/'raw.wav'
    monkeypatch.setattr(providers, '_request_voice_audio', request)
    monkeypatch.setattr(providers, 'probe', lambda p: {'duration': 1})
    with pytest.raises(providers.DurationMismatchError):
        providers.generate_voice_audio(None, {}, '/wrapper_1', tmp_path/'out.wav', lambda *a: None,
                                       lambda: None, 0, 'Test', target_duration=10, engine='VieNeu')
    assert len(calls)==1
    assert not (tmp_path/'out.wav').exists()


def test_provider_error_does_not_retry_as_duration_error(tmp_path, monkeypatch):
    calls = []
    def request(*args):
        calls.append(1)
        raise RuntimeError('connection lost')
    monkeypatch.setattr(providers, '_request_voice_audio', request)
    with pytest.raises(RuntimeError, match='connection lost'):
        providers.generate_voice_audio(None, {}, '/wrapper_1', tmp_path/'out.wav', lambda *a: None,
                                       lambda: None, 0, 'Test', target_duration=10, engine='VieNeu')
    assert len(calls)==1
