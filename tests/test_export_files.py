import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend import batches, export_files, store
from backend.app import app
from backend.models import Settings


HEADERS = {'X-AIR3view': 'studio'}


def database(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    store.DATA.mkdir()
    monkeypatch.setattr(store, 'DB', store.DATA / 'studio.sqlite3')
    store.init()


def fake_render(project, report, check, **kwargs):
    exports = []
    count = project['settings']['export_part_count'] if project['settings']['export_mode'] == 'parts' else 1
    for number in range(1, count + 1):
        files = {}
        for kind, content in (('file', b'mock-video'), ('srt', b'subtitle'), ('ass', b'ass-subtitle')):
            extension = {'file': 'mp4', 'srt': 'srt', 'ass': 'ass'}[kind]
            relative = f'renders/part-{number:03d}.{extension}'
            path = store.asset(project['id'], relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            files[kind] = relative
        exports.append({'part': number, **files})
    project['exports'] = exports
    store.save(project)


def test_publish_uses_video_date_folder_and_copies_subtitles(tmp_path, monkeypatch):
    database(tmp_path, monkeypatch)
    project = store.create('Mom: Road Rage / Fight?', {'kind': 'upload', 'file': 'source.mp4'})
    fake_render(project, lambda *args: None, lambda: None)
    result = export_files.publish(project, date='2026-09-29')
    folder = Path(result['folder'])
    assert folder.name == 'Mom_Road_Rage_Fight_2026-09-29'
    assert sorted(result['files']) == ['part-001.ass', 'part-001.mp4', 'part-001.srt']
    assert all((folder / name).is_file() for name in result['files'])
    assert json.loads((folder / '.air3view-project.json').read_text())['id'] == project['id']


def test_reexport_from_parts_to_single_removes_only_old_managed_files(tmp_path, monkeypatch):
    database(tmp_path, monkeypatch)
    project = store.create('One Story', {'kind': 'upload', 'file': 'source.mp4'})
    project['settings'].update(export_mode='parts', export_part_count=2)
    fake_render(project, lambda *args: None, lambda: None)
    folder = Path(export_files.publish(project, date='2026-09-29')['folder'])
    assert len(list(folder.glob('*.mp4'))) == 2
    (folder / 'notes.txt').write_text('user file', 'utf-8')
    project['settings']['export_mode'] = 'single'
    fake_render(project, lambda *args: None, lambda: None)
    export_files.publish(project, date='2026-09-29')
    assert [path.name for path in folder.glob('*.mp4')] == ['part-001.mp4']
    assert (folder / 'notes.txt').read_text('utf-8') == 'user file'


def test_batch_export_creates_one_folder_and_supports_parts(tmp_path, monkeypatch):
    database(tmp_path, monkeypatch)
    destination = tmp_path / 'batch destination'
    destination.mkdir()
    from backend import app as app_module
    monkeypatch.setattr(app_module.providers, 'prepare_render_audio', lambda project, *args: project)
    monkeypatch.setattr(app_module, 'render', fake_render)
    settings = Settings(output_mode='single', production_workflow='plan_first',
                        narration_style='storytelling', subtitle_highlight=False).model_dump()
    batch = batches.create('Police Stories', [
        {'url': 'https://www.youtube.com/watch?v=EXiQCyqxmSE', 'title': 'First Clip'},
        {'url': 'https://www.youtube.com/watch?v=WjCVzhRMsAo', 'title': 'Second Clip'},
    ], settings)
    with store.conn() as db:
        db.execute("UPDATE batch_items SET state='completed' WHERE batch_id=?", (batch['id'],))
        db.execute("UPDATE batches SET state='completed' WHERE id=?", (batch['id'],))
    with TestClient(app) as client:
        response = client.post(f"/api/batches/{batch['id']}/export", headers=HEADERS,
                               json={'export_mode': 'parts', 'export_part_count': 2,
                                     'export_directory': str(destination)})
        assert response.status_code == 200, response.text
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = client.get(f"/api/batches/{batch['id']}", headers=HEADERS).json()
            if all(item['export_state'] == 'completed' for item in current['items']):
                break
            time.sleep(.05)
    assert all(item['export_state'] == 'completed' for item in current['items']), current
    folders = [Path(item['export_folder']) for item in current['items']]
    assert folders[0].parent == folders[1].parent
    assert folders[0].parent.parent == destination
    assert folders[0].parent.name.startswith('Police_Stories_')
    assert all(len(list(folder.glob('*.mp4'))) == 2 for folder in folders)
    assert all(len(list(folder.glob('*.srt'))) == 2 for folder in folders)
    assert all(len(list(folder.glob('*.ass'))) == 2 for folder in folders)
    from backend import preferences
    assert preferences.status()['settings']['export_directory'] == str(destination)
    assert store.create('Next batch source', {'kind': 'upload', 'file': 'source.mp4'})['settings']['export_directory'] == str(destination)


def test_single_project_export_uses_selected_storage_and_name(tmp_path, monkeypatch):
    database(tmp_path, monkeypatch)
    destination = tmp_path / 'chosen-drive'
    destination.mkdir()
    from backend import app as app_module
    monkeypatch.setattr(app_module.providers, 'prepare_render_audio', lambda project, *args: project)
    monkeypatch.setattr(app_module, 'render', fake_render)
    project = store.create('Final Story', {'kind': 'upload', 'file': 'source.mp4'})
    project['settings'].update(output_mode='single', subtitle_highlight=False,
                               export_mode='single', export_directory=str(destination))
    store.save(project)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project['id']}/jobs", headers=HEADERS,
                               json={'kind': 'export'})
        assert response.status_code == 200, response.text
        jid = response.json()['id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and store.job(jid)['state'] not in ('completed', 'failed'):
            time.sleep(.05)
    assert store.job(jid)['state'] == 'completed', store.job(jid)['error']
    folder = Path(store.read(project['id'])['export_folder'])
    assert folder.parent == destination
    assert folder.name.startswith('Final_Story_')
    assert (folder / 'part-001.mp4').is_file()
    from backend import preferences
    assert preferences.status()['settings']['export_directory'] == str(destination)
    assert store.create('Next project', {'kind': 'upload', 'file': 'source.mp4'})['settings']['export_directory'] == str(destination)


def test_selected_directory_is_used_for_single_and_batch_exports(tmp_path, monkeypatch):
    database(tmp_path, monkeypatch)
    chosen = tmp_path / 'My Exports'
    chosen.mkdir()
    project = store.create('Chosen Folder', {'kind': 'upload', 'file': 'source.mp4'})
    fake_render(project, lambda *args: None, lambda: None)
    result = export_files.publish(project, directory=str(chosen), date='2026-09-29')
    assert Path(result['folder']).parent == chosen
    batch = batches.create('My Batch', [{'url': 'https://www.youtube.com/watch?v=EXiQCyqxmSE'}],
                           Settings(output_mode='single').model_dump())
    result = export_files.publish(project, batch_id=batch['id'], directory=str(chosen), date='2026-09-29')
    assert Path(result['folder']).parent.parent == chosen
    assert Path(result['folder']).parent.name == 'My_Batch_2026-09-29'


def test_export_root_rejects_missing_directory(tmp_path):
    from pytest import raises
    with raises(ValueError, match='Thư mục lưu video'):
        export_files.export_root(directory=str(tmp_path / 'missing'))


def test_picker_returns_selected_directory_or_cancel(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace
    import pytest
    if os.name != 'nt':
        pytest.skip('Windows folder dialog')
    calls = []
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(tmp_path) if len(calls) == 1 else '', stderr='')
    monkeypatch.setattr(export_files.subprocess, 'run', run)
    assert export_files.pick_directory() == str(tmp_path)
    assert export_files.pick_directory() == ''
    assert calls[0][1]['env']['AIR3VIEW_PICKER_INITIAL']


def test_picker_endpoint_does_not_change_setting_when_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(export_files, 'pick_directory', lambda initial: '')
    with TestClient(app) as client:
        response = client.post('/api/export/pick-directory', headers=HEADERS,
                               json={'initial': str(tmp_path)})
    assert response.status_code == 200
    assert response.json() == {'directory': ''}
