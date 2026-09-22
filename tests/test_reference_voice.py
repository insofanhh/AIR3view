import copy
import pytest
from backend import store, providers, preferences, media
from backend.reference_voice import ensure_transcript


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    store.init()
    p = store.create('Reference', {'kind': 'upload', 'file': 'video.mp4'})
    path = store.asset(p['id'], 'sample.wav')
    path.write_bytes(b'sample one')
    p['settings'].update(voice_mode='clone', voice_reference=path.name, voice_reference_text='', language='English')
    preferences.save_project(p)
    return p, path


def test_auto_asr_keeps_sample_language_and_reuses_across_projects(sample, monkeypatch):
    p, path = sample
    calls = []
    def transcribe(audio, settings, check):
        calls.append(str(audio))
        return [{'text': 'Xin chào.'}, {'text': 'Đây là giọng mẫu.'}]
    monkeypatch.setattr(providers, 'transcribe', transcribe)
    ensure_transcript(p, lambda *a: None, lambda: None)
    assert p['settings']['voice_reference_text'] == 'Xin chào. Đây là giọng mẫu.'
    assert p['settings']['language'] == 'English'
    assert store.read(p['id'])['settings']['voice_reference_hash']
    store.init()
    other = store.create('Another project', {'kind': 'upload', 'file': 'video.mp4'})
    other['settings']['voice_reference_text'] = ''
    ensure_transcript(other, lambda *a: None, lambda: None)
    assert other['settings']['voice_reference_text'] == p['settings']['voice_reference_text']
    assert len(calls) == 1


def test_replace_audio_bytes_invalidates_even_same_path(sample, monkeypatch):
    p, path = sample
    calls = []
    def transcribe(*args):
        calls.append(True)
        return [{'text': 'Same words spoken by different speakers.'}]
    monkeypatch.setattr(providers, 'transcribe', transcribe)
    ensure_transcript(p, lambda *a: None, lambda: None)
    old_hash = providers.voice_hash({'text': 'New script'}, p['settings'])
    path.write_bytes(b'sample two')
    ensure_transcript(p, lambda *a: None, lambda: None)
    assert len(calls) == 2
    assert providers.voice_hash({'text': 'New script'}, p['settings']) != old_hash


def test_empty_asr_never_asks_for_manual_input_or_marks_complete(sample, monkeypatch):
    p, _ = sample
    monkeypatch.setattr(providers, 'transcribe', lambda *a: [{'text': '[Music]'}])
    with pytest.raises(ValueError, match='Hãy thay bằng audio'):
        ensure_transcript(p, lambda *a: None, lambda: None)
    assert p['settings']['voice_reference_text'] == ''
    assert not list(store.DATA.rglob('reference-transcripts/*.json'))


def test_cancellation_leaves_transcript_unset(sample, monkeypatch):
    p, _ = sample
    def transcribe(*args):
        raise media.Cancelled('cancelled')
    monkeypatch.setattr(providers, 'transcribe', transcribe)
    with pytest.raises(media.Cancelled):
        ensure_transcript(p, lambda *a: None, lambda: None)
    assert store.read(p['id'])['settings']['voice_reference_text'] == ''


def test_worker_transcript_cannot_overwrite_new_global_voice(sample, monkeypatch):
    p, _ = sample
    other = store.create('Newer settings', {'kind': 'upload', 'file': 'video.mp4'})
    store.asset(other['id'], 'other.wav').write_bytes(b'new shared voice')
    other['settings'].update(voice_reference='other.wav', voice_reference_text='', voice_reference_hash='', language='Chinese')
    preferences.save_project(other)
    before = copy.deepcopy(preferences.status())
    monkeypatch.setattr(providers, 'transcribe', lambda *a: [{'text': 'Original voice.'}])
    ensure_transcript(p, lambda *a: None, lambda: None)
    assert preferences.status() == before
