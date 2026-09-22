from pathlib import Path
import pytest
from backend import store, providers, media
from backend.models import Narration


@pytest.fixture
def voice_project(tmp_path, monkeypatch):
    import gradio_client
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'test.sqlite3')
    store.init()
    project = store.create('Shared narrator', {'kind': 'upload', 'file': 'source.mp4'})
    project['settings']['language'] = 'English'
    project['narrations'] = [Narration(id=f'n{i}', start=i*10, text=text).model_dump()
                             for i, text in enumerate(('First sentence.', 'Second sentence.'))]
    calls = []
    output = tmp_path/'generated.wav'
    output.write_bytes(b'audio')

    class Job:
        def done(self): return True
        def result(self): return str(output)

    class Client:
        def __init__(self, *args, **kwargs): pass
        def view_api(self, **kwargs):
            return {'named_endpoints': {'/_design_fn': {'parameters': [
                {'parameter_name': 'gen', 'parameter_default': 'Auto'}]}}}
        def submit(self, **kwargs):
            calls.append(kwargs)
            return Job()

    monkeypatch.setattr(gradio_client, 'Client', Client)
    monkeypatch.setattr(gradio_client, 'handle_file', lambda path: path)
    monkeypatch.setattr(providers, 'probe', lambda path: {'duration': 3})
    monkeypatch.setattr(media, 'run', lambda args, **kwargs: Path(args[-1]).write_bytes(b'audio'))
    monkeypatch.setattr(providers, 'transcribe', lambda *args, **kwargs: [])
    return project, calls


def test_design_once_then_same_reference_for_all_segments_and_edits(voice_project):
    project, calls = voice_project
    providers.synthesize(project, lambda *a: None, lambda: None)
    assert [c['api_name'] for c in calls] == ['/_design_fn', '/_clone_fn', '/_clone_fn']
    assert calls[1]['ref_aud'] == calls[2]['ref_aud']
    assert calls[1]['ref_text'] == calls[0]['text'] == calls[2]['ref_text']
    assert calls[1]['instruct'] == calls[2]['instruct'] == ''
    reference = calls[1]['ref_aud']
    reloaded = store.read(project['id'])
    reloaded['narrations'][1]['text'] = 'Revised second sentence.'
    reloaded['settings']['voice_speed'] = 1.1
    providers.synthesize(reloaded, lambda *a: None, lambda: None, only_id='n1')
    assert calls[-1]['api_name'] == '/_clone_fn'
    assert calls[-1]['ref_aud'] == reference
    assert sum(c['api_name'] == '/_design_fn' for c in calls) == 1


def test_gender_change_creates_new_reference_and_return_reuses_original(voice_project):
    project, calls = voice_project
    providers.synthesize(project, lambda *a: None, lambda: None)
    first = calls[-1]['ref_aud']
    project['settings']['voice_gender'] = 'Female / 女'
    providers.synthesize(project, lambda *a: None, lambda: None)
    assert calls[-1]['ref_aud'] != first
    assert sum(c['api_name'] == '/_design_fn' for c in calls) == 2
    project['settings']['voice_gender'] = 'Auto'
    project['narrations'][0]['text'] = 'Updated opening.'
    providers.synthesize(project, lambda *a: None, lambda: None, only_id='n0')
    assert calls[-1]['ref_aud'] == first
    assert sum(c['api_name'] == '/_design_fn' for c in calls) == 2


def test_clone_mode_uses_uploaded_reference_for_every_segment(voice_project):
    project, calls = voice_project
    reference = store.project_dir(project['id'])/'uploaded.wav'
    reference.write_bytes(b'reference')
    project['settings'].update(voice_mode='clone', voice_reference=reference.name,
                               voice_reference_text='Reference words.')
    providers.synthesize(project, lambda *a: None, lambda: None)
    assert [c['api_name'] for c in calls] == ['/_clone_fn', '/_clone_fn']
    assert all(c['ref_aud'] == str(reference) for c in calls)


def test_missing_saved_reference_does_not_silently_change_speaker(voice_project):
    project, calls = voice_project
    providers.synthesize(project, lambda *a: None, lambda: None)
    Path(calls[-1]['ref_aud']).unlink()
    project['narrations'][0]['text'] = 'Another opening.'
    count = len(calls)
    with pytest.raises(ValueError, match='giọng chung'):
        providers.synthesize(project, lambda *a: None, lambda: None)
    assert len(calls) == count


def test_old_independent_design_cache_is_regenerated(voice_project):
    project, calls = voice_project
    settings = {k: v for k, v in project['settings'].items()
                if k.startswith('voice_') or k in ('language', 'omnivoice_url')}
    settings['voice_volume'] = 1.0
    folder = store.project_dir(project['id'])/'voices'
    folder.mkdir()
    for n in project['narrations']:
        old_hash = providers.digest({'text': n['text'], 'settings': settings})
        (folder/(old_hash+'.wav')).write_bytes(b'old random speaker')
        n.update(audio_hash=old_hash, audio='voices/'+old_hash+'.wav', caption_version=3)
    providers.synthesize(project, lambda *a: None, lambda: None)
    assert [c['api_name'] for c in calls] == ['/_design_fn', '/_clone_fn', '/_clone_fn']


def test_alignment_failure_keeps_shared_reference_and_completed_audio(voice_project, monkeypatch):
    project, calls = voice_project
    def fail(*args, **kwargs):
        raise RuntimeError('Alignment interrupted')
    monkeypatch.setattr(providers, 'transcribe', fail)
    with pytest.raises(RuntimeError, match='Alignment interrupted'):
        providers.synthesize(project, lambda *a: None, lambda: None)
    saved = store.read(project['id'])
    reference = calls[-1]['ref_aud']
    monkeypatch.setattr(providers, 'transcribe', lambda *a, **kw: [])
    providers.synthesize(saved, lambda *a: None, lambda: None)
    assert [c['api_name'] for c in calls] == ['/_design_fn', '/_clone_fn', '/_clone_fn']
    assert calls[-1]['ref_aud'] == reference


def test_story_narration_requests_exact_slot_without_silence_trimming(voice_project):
    project, calls = voice_project
    for n in project['narrations']:
        n['target_duration'] = 3
    providers.synthesize(project, lambda *a: None, lambda: None)
    assert all(c['du'] == 3 and c['po'] is False for c in calls[1:])


def test_large_tts_duration_mismatch_is_rejected(voice_project):
    project, calls = voice_project
    project['narrations'][0]['target_duration'] = 10
    with pytest.raises(ValueError, match='Thời lượng giọng'):
        providers.synthesize(project, lambda *a: None, lambda: None)


def test_old_paragraph_captions_are_regrouped_without_new_voice(voice_project, monkeypatch):
    from backend import captions
    project, calls = voice_project
    providers.synthesize(project, lambda *a: None, lambda: None)
    count = len(calls)
    project['metadata'] = {'has_audio': False}
    project['transcript'] = []
    n = project['narrations'][0]
    n['caption_version'] = 3
    n['cues'] = [{'id':'old', 'start':0, 'end':3, 'text':n['text']}]
    phrases = [{'id':'c0','start':0,'end':1,'text':'First'},
               {'id':'c1','start':1,'end':3,'text':'sentence.'}]
    monkeypatch.setattr(captions,'transcribe',lambda *a, **kw:phrases)
    captions.refresh(project,lambda *a:None,lambda:None)
    assert len(n['cues']) == 2
    assert n['caption_version'] == 4
    assert len(calls) == count


def test_export_reuses_mix_only_changes_but_regenerates_changed_reference(voice_project):
    project, calls = voice_project
    project['settings']['output_mode'] = None
    providers.synthesize(project, lambda *a:None, lambda:None)
    count = len(calls)
    project['settings'].update(duck_volume=.2, voice_volume=.8)
    providers.prepare_render_audio(project, lambda *a:None, lambda:None)
    assert len(calls) == count
    reference = store.project_dir(project['id'])/'new-speaker.wav'
    reference.write_bytes(b'reference')
    project['settings'].update(voice_mode='clone',voice_reference=reference.name,
        voice_reference_text='Actual sample words.',voice_instruct='Giọng kể tự nhiên, rõ ràng, cuốn hút.')
    providers.prepare_render_audio(project, lambda *a:None, lambda:None)
    assert len(calls) == count+2
    assert all(c['api_name']=='/_clone_fn' and c['instruct']=='' for c in calls[count:])
    assert all(n['audio_hash']==providers.voice_hash(n,project['settings']) for n in project['narrations'])


def test_export_does_not_generate_audio_for_stale_story_plan(voice_project):
    project, calls = voice_project
    with pytest.raises(ValueError,match='Cấu hình đầu ra'):
        providers.prepare_render_audio(project,lambda *a:None,lambda:None)
    assert calls == []


def test_clone_missing_reference_file_fails_before_tts(voice_project):
    project, calls = voice_project
    project['settings'].update(voice_mode='clone',voice_reference='sample.wav',voice_reference_text='')
    with pytest.raises(ValueError,match='Không tìm thấy audio'):
        providers.synthesize(project,lambda *a:None,lambda:None)
    assert calls == []
