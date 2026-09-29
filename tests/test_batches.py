from io import BytesIO
import importlib
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend import batches, store
from backend.media import FFMPEG, run
from backend.app import app, overall_progress
from backend.models import Settings


URL_A = 'https://www.youtube.com/watch?v=EXiQCyqxmSE'
URL_B = 'https://youtu.be/WjCVzhRMsAo'


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    store.init()
    return tmp_path


def settings():
    return Settings(output_mode='single', production_workflow='plan_first',
                    narration_style='storytelling').model_dump()


def test_batch_import_validates_duplicate_and_each_url(database):
    client = TestClient(app)
    response = client.post('/api/batches/preview', json={'items': [
        {'url': URL_A}, {'url': 'https://youtu.be/EXiQCyqxmSE?t=5'},
        {'url': 'https://evil.example/?v=EXiQCyqxmSE'},
    ], 'settings': settings()}, headers={'X-AIR3view': 'studio'})
    assert response.status_code == 200
    assert [row['valid'] for row in response.json()] == [True, False, False]
    with pytest.raises(ValueError, match='Dòng 2'):
        batches.create('Duplicate', [{'url': URL_A}, {'url': URL_A}], settings())
    assert not store.list_projects()


def test_batch_api_creates_snapshots_and_controls_one_item(database):
    client = TestClient(app)
    headers = {'X-AIR3view': 'studio'}
    created = client.post('/api/batches', json={'name': 'API batch', 'items': [
        {'url': URL_A}, {'url': URL_B, 'settings': {'summary_seconds': 60}}],
        'settings': settings()}, headers=headers)
    assert created.status_code == 200
    batch = created.json()
    assert len(batch['items']) == 2
    assert client.get('/api/batches').json()[0]['id'] == batch['id']
    first = batch['items'][0]
    cancelled = client.post(f"/api/batches/{batch['id']}/control",
                            json={'action': 'cancel_item', 'item_id': first['id']}, headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()['items'][0]['state'] == 'cancelled'
    retried = client.post(f"/api/batches/{batch['id']}/control",
                          json={'action': 'retry', 'item_id': first['id']}, headers=headers)
    assert retried.status_code == 200
    assert retried.json()['items'][0]['state'] == 'pending'
    project = store.read(batch['items'][1]['project_id'])
    assert project['settings']['summary_seconds'] == 60


def test_reaction_batch_uses_commentary_configuration_for_each_video(database):
    common = settings()
    common.update(editorial_mode='standard', production_workflow='legacy',
                  narration_style='highlights', original_dialogue_ratio=.8)
    rows = [{'url': URL_A, 'settings': {'editorial_mode': 'reaction_cops',
                                      'reaction_commentary_count': 3}}]
    client = TestClient(app)
    headers = {'X-AIR3view': 'studio'}
    preview = client.post('/api/batches/preview', json={'items': rows, 'settings': common}, headers=headers)
    assert preview.status_code == 200 and preview.json()[0]['valid']
    created = client.post('/api/batches', json={'items': rows, 'settings': common}, headers=headers)
    assert created.status_code == 200
    stored = store.read(created.json()['items'][0]['project_id'])['settings']
    assert stored['editorial_mode'] == 'reaction_cops'
    assert stored['reaction_commentary_count'] == 3
    assert stored['production_workflow'] == 'plan_first'
    assert stored['narration_style'] == 'storytelling'
    assert stored['opening_delay'] == 0


def test_batch_runs_in_background_without_browser_polling(database, monkeypatch):
    module = importlib.import_module('backend.app')
    from backend import media

    def prepare(project, report, check):
        project['frames'] = [{'time': 0, 'file': 'frame.jpg'}]
        project['metadata'] = {'duration': 10, 'has_audio': True}
        return store.save(project)

    def analyze(project, kind, report, check):
        project['narrations'] = [{'id': 'n1', 'text': 'Hello'}]
        return store.save(project)

    def render(project, report, check, **kwargs):
        store.asset(project['id'], 'part-001.mp4').write_bytes(b'mock-mp4')
        project['exports'] = [{'file': 'part-001.mp4', 'part': 1}]
        store.save(project)

    monkeypatch.setattr(module, 'prepare', prepare)
    monkeypatch.setattr(module, 'prepare_job_story', analyze)
    monkeypatch.setattr(module.providers, 'localize', lambda project, *a: project)
    monkeypatch.setattr(module.providers, 'synthesize', lambda project, *a: project)
    monkeypatch.setattr(module, 'render', render)
    monkeypatch.setattr(media, 'probe', lambda path: {'width': 1080, 'has_audio': True, 'duration': 1})
    batch_settings = settings()
    batch_settings['subtitle_highlight'] = False
    with TestClient(app) as client:
        response = client.post('/api/batches', json={'name': 'Background', 'items': [{'url': URL_A}],
                               'settings': batch_settings}, headers={'X-AIR3view': 'studio'})
        assert response.status_code == 200
        batch_id = response.json()['id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = client.get(f'/api/batches/{batch_id}').json()
            if current['items'][0]['state'] == 'completed':
                break
            time.sleep(.1)
        assert current['items'][0]['state'] == 'completed'


def test_batch_scheduler_is_durable_isolated_and_respects_pause(database):
    batch = batches.create('Two videos', [{'url': URL_A}, {'url': URL_B,
                           'title': 'Second', 'settings': {'summary_seconds': 90}}], settings())
    first, second = batch['items']
    assert store.read(first['project_id'])['settings']['summary_seconds'] == 180
    assert store.read(second['project_id'])['settings']['summary_seconds'] == 90
    assert store.read(second['project_id'])['settings']['title'] == 'Second'
    queued = []
    batches.tick(queued.append)
    assert len(queued) == 2
    batches.control(batch['id'], 'pause')
    store.update_job(queued[0], state='failed', error='Bad URL')
    store.update_job(queued[1], state='cancelled')
    batches.tick(queued.append)
    assert [item['state'] for item in batches.get(batch['id'])['items']] == ['failed', 'cancelled']
    batches.control(batch['id'], 'retry', first['id'])
    batches.control(batch['id'], 'pause')
    batches.tick(queued.append)
    assert len(queued) == 2  # Paused item is not scheduled.
    batches.control(batch['id'], 'resume')
    batches.tick(queued.append)
    assert len(queued) == 3
    assert batches.get(batch['id'])['items'][0]['state'] == 'queued'


def test_restart_reclaims_interrupted_batch_job(database):
    batch = batches.create('Restart', [{'url': URL_A}], settings())
    queued = []
    batches.tick(queued.append)
    store.update_job(queued[0], state='running')
    store.init()  # App startup marks a running job interrupted.
    batches.tick(queued.append)
    assert len(queued) == 2
    assert batches.get(batch['id'])['items'][0]['job_id'] == queued[-1]


def test_queued_job_is_claimed_once_and_survives_restart(database):
    batch = batches.create('Queued', [{'url': URL_A}], settings())
    queued = []
    batches.tick(queued.append)
    jid = queued[0]
    store.init()
    assert store.job(jid)['state'] == 'queued'
    assert store.claim_job(jid)
    assert not store.claim_job(jid)


def test_missing_job_does_not_leave_batch_stuck(database):
    batch = batches.create('Missing job', [{'url': URL_A}], settings())
    queued = []
    batches.tick(queued.append)
    with store.conn() as db:
        db.execute('DELETE FROM jobs WHERE id=?', (queued[0],))
    batches.tick(queued.append)
    item = batches.get(batch['id'])['items'][0]
    assert item['state'] == 'failed'
    assert batches.get(batch['id'])['state'] == 'completed_with_errors'


@pytest.mark.skipif(not shutil.which(FFMPEG), reason='FFmpeg required')
def test_completed_item_requires_real_video_and_audio(database):
    batch = batches.create('Export', [{'url': URL_A}], settings())
    item = batch['items'][0]
    queued = []
    batches.tick(queued.append)
    project = store.read(item['project_id'])
    video = store.asset(project['id'], 'finished.mp4')
    run([FFMPEG, '-y', '-f', 'lavfi', '-i', 'color=size=64x64:rate=30:duration=0.5',
         '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000',
         '-t', '0.5', '-c:v', 'libx264', '-c:a', 'aac', video])
    project['exports'] = [{'file': 'finished.mp4', 'part': 1}]
    store.save(project)
    store.update_job(queued[0], state='completed')
    batches.tick(queued.append)
    assert batches.get(batch['id'])['state'] == 'completed'
    assert batches.get(batch['id'])['items'][0]['exports'][0]['file'] == 'finished.mp4'


def test_transient_and_recoverable_pipeline_errors_auto_retry(database):
    assert batches.retryable_error('HTTP 503 Service unavailable')
    assert batches.retryable_error('Connection timed out')
    assert batches.retryable_error('story-sel26 (1.68s → 1.99s): AI sửa thời lượng không hợp lệ sau giới hạn retry')
    assert batches.retryable_error('scene[0] là point nhưng không khớp mốc ảnh được gửi.')
    assert batches.retryable_error('scene[0] có end nhỏ hơn start.')
    assert batches.retryable_error('[WinError 5] Access is denied: voice-repair-attempts/file.tmp')
    assert not batches.retryable_error('OpenAI 429: Request too large, Requested 11977 Limit 10000 on tokens per min')
    assert not batches.retryable_error('Invalid API key')
    batch = batches.create('Transient', [{'url': URL_A}], settings())
    queued = []
    batches.tick(queued.append)
    store.update_job(queued[0], state='failed', error='HTTP 503 Service unavailable')
    batches.tick(queued.append)
    item = batches.get(batch['id'])['items'][0]
    assert item['state'] == 'pending' and item['attempts'] == 1
    assert item['retry_limit'] == batches.MAX_TRANSIENT_RETRIES
    assert 14 <= item['ready_at'] - time.time() <= 15
    batches.tick(queued.append)
    assert len(queued) == 1
    with store.conn() as db:
        db.execute('UPDATE batch_items SET ready_at=0 WHERE id=?', (item['id'],))
    batches.tick(queued.append)
    assert len(queued) == 2


def test_voice_failure_retries_five_times_after_three_seconds_then_stops(database):
    batch = batches.create('Recover voice', [{'url': URL_A}], settings())
    queued = []
    batches.tick(queued.append)
    item_id = batch['items'][0]['id']
    failure = 'story-sel26 (1.68s → 1.99s): AI sửa thời lượng không hợp lệ sau giới hạn retry'
    for attempt in range(1, 6):
        store.update_job(queued[-1], state='failed', error=failure)
        batches.tick(queued.append)
        item = batches.get(batch['id'])['items'][0]
        assert item['state'] == 'pending'
        assert item['attempts'] == attempt
        assert item['retry_limit'] == 5
        assert 2 <= item['ready_at'] - time.time() <= 3
        batches.tick(queued.append)
        assert len(queued) == attempt  # No immediate retry before deadline.
        with store.conn() as db:
            db.execute('UPDATE batch_items SET ready_at=0 WHERE id=?', (item_id,))
        batches.tick(queued.append)
        assert len(queued) == attempt + 1
    store.update_job(queued[-1], state='failed', error=failure)
    batches.tick(queued.append)
    assert batches.get(batch['id'])['items'][0]['state'] == 'failed'
    assert batches.get(batch['id'])['state'] == 'completed_with_errors'
    batches.tick(queued.append)
    assert len(queued) == 6


def test_existing_failed_item_is_adopted_and_manual_retry_resets_budget(database):
    batch = batches.create('Existing failed', [{'url': URL_A}], settings())
    item_id = batch['items'][0]['id']
    with store.conn() as db:
        db.execute("UPDATE batch_items SET state='failed',error=? WHERE id=?",
                   ('scene[0] là point nhưng không khớp mốc ảnh được gửi.', item_id))
        db.execute("UPDATE batches SET state='completed_with_errors' WHERE id=?", (batch['id'],))
    queued = []
    batches.tick(queued.append)
    item = batches.get(batch['id'])['items'][0]
    assert item['state'] == 'pending' and item['attempts'] == 1
    assert batches.get(batch['id'])['state'] == 'running'
    with store.conn() as db:
        db.execute("UPDATE batch_items SET state='failed',attempts=5 WHERE id=?", (item_id,))
    batches.tick(queued.append)
    assert batches.get(batch['id'])['items'][0]['state'] == 'failed'
    retried = batches.control(batch['id'], 'retry', item_id)['items'][0]
    assert retried['state'] == 'pending' and retried['attempts'] == 0


def test_cancelled_batch_never_auto_restarts_failed_job(database):
    batch = batches.create('Cancelled', [{'url': URL_A}], settings())
    queued = []
    batches.tick(queued.append)
    batches.control(batch['id'], 'cancel')
    store.update_job(queued[0], state='failed', error='AI sửa thời lượng không hợp lệ sau giới hạn retry')
    batches.tick(queued.append)
    item = batches.get(batch['id'])['items'][0]
    assert item['state'] == 'failed' and item['attempts'] == 0
    assert batches.get(batch['id'])['state'] == 'cancelled'
    batches.tick(queued.append)
    assert len(queued) == 1


def test_all_job_progress_stays_monotonic_across_stages():
    points = [('prepare', 95), ('analyze', 5), ('analyze', 90),
              ('voice', 10), ('render', 2), ('render', 100)]
    value = 0
    history = []
    for stage, local in points:
        value = overall_progress('all', stage, local, value)
        history.append(value)
    assert history == sorted(history)
    assert history[-1] == 99


def test_excel_template_roundtrip_and_api(database):
    client = TestClient(app)
    response = client.get('/api/batches/template')
    assert response.status_code == 200
    rows = batches.parse_excel(response.content)
    assert rows[0]['url'] == URL_A
    assert rows[0]['settings']['summary_seconds'] == 180
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['url', 'title', 'summary_minutes', 'hook_enabled'])
    sheet.append([URL_B, 'Second', 2, False])
    output = BytesIO()
    workbook.save(output)
    imported = client.post('/api/batches/import-excel', files={'file': ('links.xlsx', output.getvalue())},
                           headers={'X-AIR3view': 'studio'})
    assert imported.status_code == 200
    assert imported.json()[0]['settings'] == {'summary_seconds': 120, 'hook_enabled': False}
