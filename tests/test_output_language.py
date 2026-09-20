import json
import pytest
from backend import store, providers


def test_usage_limit_error_explains_retry_and_cache():
    message = providers.codex_error("ERROR: You've hit your usage limit. Try again tomorrow.")
    assert 'hết hạn mức' in message and 'cache' in message
    assert 'codex login' not in message


def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'test.sqlite3')
    store.init()
    p = store.create('Test', {'kind': 'upload', 'file': 'source.mp4'})
    p['settings'].update(output_mode=None, language='English', title='Cuộc đối thoại')
    p['narrations'] = [{'id': 'n1', 'start': 5, 'text': 'Cảnh sát bước đến.'}]
    p['transcript'] = [{'id': 'c1', 'start': 1, 'end': 3, 'text': 'Xin chào.'}]
    return p


def test_localize_preserves_ids_timing_and_source(tmp_path, monkeypatch):
    p = sample(tmp_path, monkeypatch)
    monkeypatch.setattr(providers, 'ask_ai', lambda *args: {'items': [
        {'id': 'title', 'text': 'The conversation'},
        {'id': 'n0', 'text': 'The officer approaches.'},
        {'id': 'c0', 'text': 'Hello.'}]})
    out = providers.localize(p, lambda *a: None, lambda: None)
    assert out['settings']['title'] == 'The conversation'
    assert out['narrations'][0]['start'] == 5
    assert out['transcript'][0] == {'id': 'c1', 'start': 1, 'end': 3, 'text': 'Hello.'}
    assert out['source_transcript'][0]['text'] == 'Xin chào.'
    assert out['script_language'] == out['transcript_language'] == 'English'


def test_incomplete_translation_is_not_applied(tmp_path, monkeypatch):
    p = sample(tmp_path, monkeypatch)
    monkeypatch.setattr(providers, 'ask_ai', lambda *args: {'items': [{'id': 'title', 'text': 'Changed'}]})
    with pytest.raises(ValueError, match='thiếu/thừa'):
        providers.localize(p, lambda *a: None, lambda: None)
    assert p['settings']['title'] == 'Cuộc đối thoại'


def test_matching_script_and_channel_caption_language_need_no_ai_call(tmp_path, monkeypatch):
    p = sample(tmp_path, monkeypatch)
    p.update(script_language='English', transcript_origin='youtube_subtitles', source_subtitles='captions.en-channel.srt')
    p['settings']['title'] = 'The conversation'
    p['transcript'][0]['text'] = 'Hello.'
    p['narrations'][0]['text'] = 'The officer approaches.'
    def unexpected(*args):
        raise AssertionError('Already-English content should not be sent for translation')
    monkeypatch.setattr(providers, 'ask_ai', unexpected)
    out = providers.localize(p, lambda *a: None, lambda: None)
    assert out['transcript_language'] == 'English'


def test_legacy_freeze_projects_migrate_to_moving_muted_overlay(tmp_path, monkeypatch):
    p = sample(tmp_path, monkeypatch)
    p.pop('playback_version')
    p['settings'].update(narration_mode='insert', duck_volume=.12)
    store.save(p)
    store.init()
    out = store.read(p['id'])
    assert out['settings']['narration_mode'] == 'overlay'
    assert out['settings']['duck_volume'] == 0


def test_ai_cannot_automatically_switch_to_freeze(tmp_path, monkeypatch):
    p = sample(tmp_path, monkeypatch)
    p['settings']['review_enabled'] = False
    p['metadata'] = {'duration': 20, 'has_audio': True}
    p['frames'] = [{'time': 0, 'file': 'frame.jpg'}]
    answer = {'scenes': [], 'narrations': [{'start': 5, 'text': 'The officer approaches.', 'evidence': 'frame 0'}], 'hooks': [], 'summary': 'A conversation.', 'requires_insert': True}
    monkeypatch.setattr(providers, 'ask_ai', lambda *args: answer)
    out = providers.analyze(p, lambda *a: None, lambda: None)
    assert out['settings']['narration_mode'] == 'overlay'
    assert out['settings']['duck_volume'] == 0


def test_reanalysis_translates_preserved_title(tmp_path, monkeypatch):
    p = sample(tmp_path, monkeypatch)
    p.update(script_language='Vietnamese', transcript_language='English')
    p['settings']['review_enabled'] = False
    p['metadata'] = {'duration': 20, 'has_audio': True}
    p['frames'] = [{'time': 0, 'file': 'frame.jpg'}]
    answers = iter([
        {'scenes': [], 'narrations': [], 'hooks': [], 'summary': 'A conversation.', 'requires_insert': False},
        {'items': [{'id': 'title', 'text': 'The conversation'}]},
    ])
    monkeypatch.setattr(providers, 'ask_ai', lambda *args: next(answers))
    out = providers.analyze(p, lambda *a: None, lambda: None)
    assert out['title_language'] == 'Vietnamese'
    out = providers.localize(out, lambda *a: None, lambda: None)
    assert out['settings']['title'] == 'The conversation'
    assert out['title_language'] == 'English'


def test_export_rejects_untranslated_language_change(tmp_path, monkeypatch):
    from backend.timeline import build
    p = sample(tmp_path, monkeypatch)
    p.update(script_language='Vietnamese', transcript_language='English')
    with pytest.raises(ValueError, match='Ngôn ngữ nội dung'):
        build(p, strict=True)


def test_audio_cache_survives_alignment_failure(tmp_path, monkeypatch):
    import gradio_client
    p = sample(tmp_path, monkeypatch)
    n = p['narrations'][0]
    n.update(enabled=True, audio='', audio_hash='', duration=0, cues=[])
    fingerprint = providers.voice_hash(n, p['settings'])
    audio = store.project_dir(p['id']) / 'voices' / (fingerprint + '.wav')
    audio.parent.mkdir()
    audio.write_bytes(b'cached TTS')
    monkeypatch.setattr(providers, 'probe', lambda path: {'duration': 2})
    def unexpected(*args, **kwargs):
        raise AssertionError('Cached audio must not call OmniVoice again')
    monkeypatch.setattr(gradio_client, 'Client', unexpected)
    def fail_alignment(*args, **kwargs):
        raise RuntimeError('ASR interrupted')
    monkeypatch.setattr(providers, 'transcribe', fail_alignment)
    with pytest.raises(RuntimeError, match='ASR interrupted'):
        providers.synthesize(p, lambda *a: None, lambda: None)
    saved = store.read(p['id'])
    assert saved['narrations'][0]['audio_hash'] == fingerprint
    assert saved['narrations'][0]['caption_version'] == 0
    monkeypatch.setattr(providers, 'transcribe', lambda *a, **kw: [{'id':'c0', 'start':0, 'end':2, 'text':n['text']}])
    result = providers.synthesize(saved, lambda *a: None, lambda: None)
    assert result['narrations'][0]['caption_version'] == 3
    assert audio.read_bytes() == b'cached TTS'
