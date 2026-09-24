import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import store, preferences
from backend.app import app

HEADERS={'X-AIR3view':'studio'}


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'studio.sqlite3')
    store.init()
    return TestClient(app)


def delete(client,p,**overrides):
    return client.request('DELETE','/api/projects/'+p['id'],headers=HEADERS,
                          json={'revision':p['revision'],'confirm':True,**overrides})


def test_delete_removes_only_target_assets_and_jobs_preserves_shared_voice(env):
    p=store.create('Delete me',{'kind':'upload','file':'source.mp4'})
    other=store.create('Keep me',{'kind':'upload','file':'source.mp4'})
    folder=store.project_dir(p['id'])
    (folder/'source.mp4').write_bytes(b'source')
    (folder/'renders').mkdir();(folder/'renders/out.mp4').write_bytes(b'export')
    (folder/'reference.wav').write_bytes(b'shared voice')
    p['settings']['voice_reference']='reference.wav'
    p=preferences.save_project(p)
    shared=preferences.current()['reference']['file']
    outside=store.DATA/'original-upload.mp4';outside.write_bytes(b'outside')
    jid=store.new_job(p['id'],'prepare');store.update_job(jid,state='completed')
    keep=store.project_dir(other['id'])/'source.mp4';keep.write_bytes(b'keep')
    response=delete(env,p)
    assert response.status_code==200 and response.json()['deleted']
    assert not folder.exists() and keep.read_bytes()==b'keep' and outside.read_bytes()==b'outside'
    assert (store.DATA/'_preferences'/shared).read_bytes()==b'shared voice'
    assert preferences.current()['source_project_id'] is None
    assert env.get('/api/projects/'+p['id']).status_code==404
    assert env.get('/api/projects/'+p['id']+'/jobs').json()==[]
    with pytest.raises(KeyError):store.new_job(p['id'],'render')


@pytest.mark.parametrize('state',['queued','running'])
def test_busy_project_cannot_be_deleted(env,state):
    p=store.create('Busy',{'file':'source.mp4'})
    jid=store.new_job(p['id'],'render');store.update_job(jid,state=state)
    assert delete(env,p).status_code==409
    assert store.read(p['id']) and store.project_dir(p['id']).exists()


def test_requires_confirmation_header_and_latest_revision(env):
    p=store.create('Keep',{'file':'source.mp4'})
    assert env.request('DELETE','/api/projects/'+p['id'],json={'revision':p['revision'],'confirm':True}).status_code==403
    assert delete(env,p,confirm=False).status_code==422
    old=p['revision'];store.save(p)
    assert delete(env,p,revision=old).status_code==409
    assert store.read(p['id'])


def test_locked_file_failure_keeps_record_for_retry(env,monkeypatch):
    p=store.create('Locked',{'file':'source.mp4'})
    def fail(*args,**kwargs):raise PermissionError('file locked')
    monkeypatch.setattr(store.shutil,'rmtree',fail)
    assert delete(env,p).status_code==409
    assert store.read(p['id'])


def test_missing_directory_still_removes_database_entry(env):
    p=store.create('No files',{'file':'source.mp4'})
    (store.DATA/p['id']).rmdir()
    assert delete(env,p).status_code==200


def test_invalid_project_path_is_rejected_before_filesystem_access(env):
    for pid in ('../_preferences','', 'a'*31, 'g'*32):
        with pytest.raises(ValueError):store.delete_project(pid,0)


def test_linked_project_is_not_followed(env,monkeypatch):
    p=store.create('Linked',{'file':'source.mp4'})
    target=store.DATA/p['id']
    original=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda path:True if path==target else original(path))
    assert delete(env,p).status_code==422
    assert target.exists() and store.read(p['id'])
