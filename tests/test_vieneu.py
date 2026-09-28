from pathlib import Path

import pytest

from backend import providers, store, vieneu
from backend.models import Settings


def test_sdk_clone_needs_audio_but_no_transcript(tmp_path):
    reference = tmp_path / 'reference.wav'
    reference.write_bytes(b'audio')
    settings = Settings(voice_mode='clone').model_dump()
    params = vieneu.infer_parameters(settings, 'Xin chào.', reference)
    assert params['ref_audio'] == str(reference.resolve())
    assert 'voice' not in params and 'ref_text' not in params
    assert params['temperature'] == .8


def test_sdk_default_voice_and_explicit_voice():
    settings = Settings().model_dump()
    assert vieneu.infer_parameters(settings, 'Hi')['voice'] == 'Minh Quân'
    settings['vieneu_voice'] = 'Mai Anh'
    assert vieneu.infer_parameters(settings, 'Hi')['voice'] == 'Mai Anh'


def test_sdk_rejects_missing_reference_and_unsupported_language():
    with pytest.raises(ValueError, match='tham chiếu'):
        vieneu.infer_parameters(Settings(voice_mode='clone').model_dump(), 'Hi')
    with pytest.raises(ValueError, match='English'):
        vieneu.infer_parameters(Settings(language='Chinese').model_dump(), 'Hi')


def test_sdk_cache_ignores_old_url_and_transcript_but_tracks_device():
    settings = Settings().model_dump()
    n = {'text': 'Hi'}
    baseline = providers.voice_hash(n, settings)
    settings.update(vieneu_url='http://localhost:7860', voice_reference_text='Irrelevant')
    assert providers.voice_hash(n, settings) == baseline
    settings['vieneu_device'] = 'cuda'
    assert providers.voice_hash(n, settings) != baseline


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
