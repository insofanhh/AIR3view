import json

import pytest
from fastapi.testclient import TestClient

from backend import store
from backend.app import app


HEADERS = {'X-AIR3view': 'studio'}
SHARED_EXAMPLE = {
    # Output
    'output_mode': 'parts', 'summary_seconds': 240, 'part_count': 4,
    'part_seconds': 75, 'split_mode': 'exact', 'opening_delay': 6,
    # Layout
    'background': '#123456', 'background_mode': 'color', 'layout_preset': 'classic',
    'fit': 'contain', 'crop_x': 42, 'crop_y': 58, 'title_size': 44,
    'subtitle_size': 52, 'subtitle_color': '#abcdef', 'subtitle_position': 'below',
    'subtitles': False, 'source_subtitle_blur': True,
    'subtitle_highlight': False, 'subtitle_highlight_color': '#fedcba',
    # Voice
    'original_volume': .8, 'duck_volume': .25, 'voice_volume': 1.2,
    'narration_mode': 'insert', 'voice_mode': 'design',
    'voice_instruct': 'Warm documentary voice', 'voice_speed': 1.1,
    'voice_gender': 'Female', 'voice_steps': 40,
    # Rules
    'narration_style': 'highlights', 'original_dialogue_ratio': .2,
    'review_enabled': False, 'draft_rule': 'Custom draft rule',
    'review_rule': 'Custom review rule', 'summary_rule': 'Custom summary rule',
    # Connections
    'provider': 'gemini', 'model': 'gemini-custom', 'language': 'French',
    'asr_model': 'medium', 'asr_device': 'cpu',
    'omnivoice_url': 'http://localhost:8001',
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    store.init()
    with TestClient(app) as test_client:
        yield test_client


def edit_payload(project):
    return {key: project[key] for key in ('revision', 'name', 'settings', 'narrations', 'transcript')}


def test_user_save_promotes_shared_fields_and_apply_preserves_source_fields(client):
    source = store.create('Source', {'kind': 'upload', 'file': 'source.mp4'})
    target = store.create('Target', {'kind': 'upload', 'file': 'source.mp4'})
    target['settings'].update(
        title='Keep target title', hook_enabled=True, hook_start=12, hook_end=18,
        part_durations=[31, 47], language='German'
    )
    target['exports'] = [{'file': 'old.mp4'}]
    target['preview_exports'] = [{'file': 'preview.mp4'}]
    target = store.save(target)

    body = edit_payload(source)
    body['settings'].update(SHARED_EXAMPLE)
    body['settings'].update(
        title='Source title',
        hook_enabled=False, hook_start=2, hook_end=7, part_durations=[60, 60]
    )
    saved = client.put(f"/api/projects/{source['id']}", json=body, headers=HEADERS)
    assert saved.status_code == 200

    applied = client.post(f"/api/projects/{target['id']}/apply-preferences", headers=HEADERS)
    assert applied.status_code == 200
    result = applied.json()
    assert result['revision'] == target['revision'] + 1
    for key, value in SHARED_EXAMPLE.items():
        assert result['settings'][key] == value
    assert result['settings']['title'] == 'Keep target title'
    assert result['settings']['hook_enabled'] is True
    assert result['settings']['hook_start'] == 12
    assert result['settings']['hook_end'] == 18
    assert result['settings']['part_durations'] == [31, 47]
    assert result['exports'] == []
    assert result['preview_exports'] == []

    unchanged = client.post(f"/api/projects/{target['id']}/apply-preferences", headers=HEADERS)
    assert unchanged.status_code == 200
    assert unchanged.json()['revision'] == result['revision']


def test_new_projects_inherit_defaults_but_background_saves_do_not_promote(client):
    source = store.create('Source', {'kind': 'upload', 'file': 'source.mp4'})
    body = edit_payload(source)
    body['settings'].update(SHARED_EXAMPLE)
    response = client.put(f"/api/projects/{source['id']}", json=body, headers=HEADERS)
    assert response.status_code == 200

    worker_copy = store.read(source['id'])
    worker_copy['settings']['language'] = 'Spanish'
    store.save(worker_copy)
    store.init()

    created = store.create('New', {'kind': 'upload', 'file': 'source.mp4'})
    for key, value in SHARED_EXAMPLE.items():
        assert created['settings'][key] == value
    assert created['settings']['title'] == ''
    assert created['settings']['hook_start'] == 0
    assert created['settings']['part_durations'] == []


def test_rejected_edits_do_not_change_preferences(client):
    project = store.create('Source', {'kind': 'upload', 'file': 'source.mp4'})
    body = edit_payload(project)
    body['settings']['language'] = 'French'
    saved = client.put(f"/api/projects/{project['id']}", json=body, headers=HEADERS)
    assert saved.status_code == 200

    body['settings']['language'] = 'Japanese'
    assert client.put(f"/api/projects/{project['id']}", json=body, headers=HEADERS).status_code == 409
    invalid = edit_payload(saved.json())
    invalid['settings']['crop_x'] = 101
    assert client.put(f"/api/projects/{project['id']}", json=invalid, headers=HEADERS).status_code == 422
    assert client.get('/api/preferences').json()['settings']['language'] == 'French'


def test_reference_upload_is_durable_and_copied_into_each_project(client):
    source = store.create('Voice source', {'kind': 'upload', 'file': 'source.mp4'})
    target = store.create('Voice target', {'kind': 'upload', 'file': 'source.mp4'})
    response = client.post(
        f"/api/projects/{source['id']}/reference",
        files={'file': ('voice.wav', b'RIFF durable voice bytes', 'audio/wav')},
        headers=HEADERS,
    )
    assert response.status_code == 200
    uploaded_project = response.json()
    source_reference = uploaded_project['settings']['voice_reference']
    body = edit_payload(uploaded_project)
    body['settings'].update(
        voice_mode='clone', voice_reference_text='Exact reference transcript',
        voice_instruct='Keep the source cadence', voice_speed=.9,
    )
    configured = client.put(f"/api/projects/{source['id']}", json=body, headers=HEADERS)
    assert configured.status_code == 200
    store.asset(source['id'], source_reference).unlink()

    status = client.get('/api/preferences')
    assert status.status_code == 200
    assert status.json()['has_voice_reference'] is True
    assert status.json()['settings']['voice_reference'] is True
    assert 'file' not in json.dumps(status.json()).lower()

    applied = client.post(f"/api/projects/{target['id']}/apply-preferences", headers=HEADERS)
    assert applied.status_code == 200
    relative = applied.json()['settings']['voice_reference']
    assert relative.startswith('preference-reference-')
    assert applied.json()['settings']['voice_mode'] == 'clone'
    assert applied.json()['settings']['voice_reference_text'] == 'Exact reference transcript'
    assert applied.json()['settings']['voice_instruct'] == 'Keep the source cadence'
    assert applied.json()['settings']['voice_speed'] == .9
    assert store.asset(target['id'], relative).read_bytes() == b'RIFF durable voice bytes'

    changed = client.post(
        f"/api/projects/{source['id']}/reference",
        files={'file': ('new.wav', b'RIFF new shared voice', 'audio/wav')},
        headers=HEADERS,
    )
    assert changed.status_code == 200
    assert store.asset(target['id'], relative).read_bytes() == b'RIFF durable voice bytes'

    created = store.create('Future project', {'kind': 'upload', 'file': 'source.mp4'})
    assert store.asset(created['id'], created['settings']['voice_reference']).read_bytes() == b'RIFF new shared voice'


def test_first_initialization_migrates_most_recent_project(client):
    older = store.create('Older', {'kind': 'upload', 'file': 'source.mp4'})
    older['settings']['language'] = 'Italian'
    store.save(older)
    latest = store.create('Latest', {'kind': 'upload', 'file': 'source.mp4'})
    latest['settings'].update(language='Japanese', title='Local only')
    latest = store.save(latest)
    reference = 'legacy-reference.mp3'
    store.asset(latest['id'], reference).write_bytes(b'legacy reference')
    latest['settings']['voice_reference'] = reference
    latest = store.save(latest)
    with store.conn() as db:
        db.execute('DELETE FROM preferences')

    store.init()
    migrated = client.get('/api/preferences').json()
    assert migrated['source_project_id'] == latest['id']
    assert migrated['settings']['language'] == 'Japanese'
    assert 'title' not in migrated['settings']
    assert migrated['has_voice_reference'] is True

    created = store.create('After migration', {'kind': 'upload', 'file': 'source.mp4'})
    assert created['settings']['language'] == 'Japanese'
    assert created['settings']['title'] == ''
    assert store.asset(created['id'], created['settings']['voice_reference']).read_bytes() == b'legacy reference'


def test_corrupt_shared_reference_is_actionable_and_does_not_wipe_project(client):
    source = store.create('Source', {'kind': 'upload', 'file': 'source.mp4'})
    target = store.create('Target', {'kind': 'upload', 'file': 'source.mp4'})
    own_reference = 'target-reference.wav'
    store.asset(target['id'], own_reference).write_bytes(b'target voice')
    target['settings']['voice_reference'] = own_reference
    target = store.save(target)
    uploaded = client.post(
        f"/api/projects/{source['id']}/reference",
        files={'file': ('shared.wav', b'shared voice', 'audio/wav')},
        headers=HEADERS,
    )
    assert uploaded.status_code == 200
    with store.conn() as db:
        preference = json.loads(db.execute('SELECT body FROM preferences').fetchone()['body'])
    (store.DATA / '_preferences' / preference['reference']['file']).unlink()

    response = client.post(f"/api/projects/{target['id']}/apply-preferences", headers=HEADERS)
    assert response.status_code == 422
    assert 'tải lại file giọng mẫu' in response.json()['detail'].lower()
    unchanged = store.read(target['id'])
    assert unchanged['revision'] == target['revision']
    assert unchanged['settings']['voice_reference'] == own_reference
    assert store.asset(target['id'], own_reference).read_bytes() == b'target voice'


def test_project_document_does_not_expose_preference_metadata(client):
    project = store.create('Document', {'kind': 'upload', 'file': 'source.mp4'})
    document = client.get(f"/api/projects/{project['id']}/document")
    assert document.status_code == 200
    payload = document.json()
    assert 'preferences' not in payload
    assert 'source_project_id' not in payload
    assert 'api_key' not in json.dumps(payload).lower()


def test_apply_preferences_rejects_busy_project(client):
    project = store.create('Busy', {'kind': 'upload', 'file': 'source.mp4'})
    store.new_job(project['id'], 'prepare')
    response = client.post(f"/api/projects/{project['id']}/apply-preferences", headers=HEADERS)
    assert response.status_code == 409


def test_reupload_same_voice_repairs_corrupt_shared_copy(client):
    project = store.create('Repair voice', {'kind': 'upload', 'file': 'source.mp4'})
    endpoint = f"/api/projects/{project['id']}/reference"
    upload = {'file': ('voice.wav', b'correct voice', 'audio/wav')}
    assert client.post(endpoint, files=upload, headers=HEADERS).status_code == 200
    with store.conn() as db:
        preference = json.loads(db.execute('SELECT body FROM preferences').fetchone()['body'])
    path = store.DATA / '_preferences' / preference['reference']['file']
    path.write_bytes(b'corrupt')
    assert client.post(endpoint, files=upload, headers=HEADERS).status_code == 200
    assert path.read_bytes() == b'correct voice'
    fresh = store.create('Repaired', {'kind': 'upload', 'file': 'source.mp4'})
    assert store.asset(fresh['id'], fresh['settings']['voice_reference']).read_bytes() == b'correct voice'
