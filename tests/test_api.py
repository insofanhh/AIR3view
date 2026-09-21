import pytest
from fastapi.testclient import TestClient
from backend import store
from backend.app import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'test.sqlite3')
    store.init()
    return TestClient(app)


def test_local_mutations_require_header(client):
    assert client.post('/api/key',json={'api_key':''}).status_code==403
    assert client.post('/api/key',json={'api_key':''},headers={'X-AIR3view':'studio','Origin':'https://evil.example'}).status_code==403


def test_revision_conflict_does_not_overwrite_newer_project(client):
    p=store.create('test',{'kind':'upload','file':'source.mp4'})
    edit={k:p[k] for k in ['revision','name','settings','narrations','transcript']}
    edit['name']='new title'
    headers={'X-AIR3view':'studio'}
    assert client.put('/api/projects/'+p['id'],json=edit,headers=headers).status_code==200
    edit['name']='old tab title'
    assert client.put('/api/projects/'+p['id'],json=edit,headers=headers).status_code==409
    assert store.read(p['id'])['name']=='new title'


def test_media_path_cannot_escape_project(client):
    p=store.create('test',{'kind':'upload','file':'source.mp4'})
    with pytest.raises(ValueError):
        store.asset(p['id'],'../../secret.mp4')
    assert client.get('/media/'+p['id']+'/studio.sqlite3').status_code==404


def test_preview_is_published_only_after_atomic_completion_and_versioned(client):
    p = store.create('Preparing', {'kind': 'upload', 'file': 'source.mp4'})
    p['metadata'] = {'duration': 10}
    store.save(p)
    folder = store.project_dir(p['id'])
    (folder/'proxy.tmp.mp4').write_bytes(b'partial')
    url = '/api/projects/' + p['id']
    assert client.get(url).json()['preview'] is None
    (folder/'proxy.tmp.mp4').replace(folder/'proxy.mp4')
    first = client.get(url).json()['preview']
    assert first['file'] == 'proxy.mp4'
    assert client.get('/api/projects').json()[0]['preview'] == first
    (folder/'proxy.mp4').write_bytes(b'new complete preview')
    assert client.get(url).json()['preview']['version'] != first['version']
    assert 'preview' not in store.read(p['id'])


def test_preview_miss_is_not_cached_and_ranges_preserve_bytes(client):
    p = store.create('Media', {'kind': 'upload', 'file': 'source.mp4'})
    url = '/media/' + p['id'] + '/proxy.mp4'
    missing = client.get(url)
    assert missing.status_code == 404
    assert missing.headers['cache-control'] == 'no-store'
    (store.project_dir(p['id'])/'proxy.mp4').write_bytes(b'0123456789')
    response = client.get(url+'?v=new', headers={'Range': 'bytes=2-5'})
    assert response.status_code == 206
    assert response.content == b'2345'
    assert response.headers['content-range'] == 'bytes 2-5/10'
    assert response.headers['content-type'] == 'video/mp4'
    assert response.headers['cache-control'] == 'no-cache'


def test_busy_project_cannot_be_edited_and_job_claim_is_unique(client):
    p=store.create('test',{'kind':'upload','file':'source.mp4'})
    jid=store.new_job(p['id'],'prepare')
    assert store.job(jid)['state']=='queued'
    with pytest.raises(ValueError):
        store.new_job(p['id'],'prepare')
    edit={k:p[k] for k in ['revision','name','settings','narrations','transcript']}
    assert client.put('/api/projects/'+p['id'],json=edit,headers={'X-AIR3view':'studio'}).status_code==409


def test_recovery_marks_interrupted_jobs(client):
    p=store.create('test',{'kind':'upload','file':'source.mp4'})
    jid=store.new_job(p['id'],'render')
    store.update_job(jid,state='running')
    store.init()
    assert store.job(jid)['state']=='interrupted'


def test_edit_cue_time_clears_stale_word_highlights(client):
    p = store.create('Words', {'kind':'upload','file':'source.mp4'})
    p['transcript'] = [{'id':'c1','start':1,'end':2,'text':'Hello','speaker':'original','words':[{'text':'Hello','start':1,'end':2}]}]
    store.save(p)
    edit = {k:p[k] for k in ['revision','name','settings','narrations','transcript']}
    edit['transcript'][0]['start'] = .5
    result = client.put('/api/projects/'+p['id'],json=edit,headers={'X-AIR3view':'studio'})
    assert result.status_code == 200
    assert result.json()['transcript'][0]['words'] == []


def test_output_settings_are_saved_before_running_and_validate_count(client):
    p=store.create('Output options',{'kind':'upload','file':'source.mp4'})
    edit={k:p[k] for k in ['revision','name','settings','narrations','transcript']}
    edit['settings'].update(output_mode='parts',part_count=4,part_seconds=75)
    r=client.put('/api/projects/'+p['id'],json=edit,headers={'X-AIR3view':'studio'})
    assert r.status_code==200
    assert r.json()['settings']['part_count']==4 and r.json()['settings']['part_seconds']==75
    assert store.jobs(p['id'])==[]
    edit['revision']=r.json()['revision'];edit['settings']['part_count']=2.5
    assert client.put('/api/projects/'+p['id'],json=edit,headers={'X-AIR3view':'studio'}).status_code==422


def test_old_project_must_choose_output_before_auto_job(client):
    from backend.app import queue_job
    p=store.create('Legacy',{'kind':'upload','file':'source.mp4'})
    p['settings']['output_mode']=None;store.save(p)
    with pytest.raises(ValueError,match='Chọn Một video'):
        queue_job(p['id'],'all')
    assert store.jobs(p['id'])==[]


def test_new_output_config_cannot_synthesize_old_script(client):
    from backend.app import queue_job
    p=store.create('New cut',{'kind':'upload','file':'source.mp4'})
    with pytest.raises(ValueError,match='Phân tích AI lại'):
        queue_job(p['id'],'voice')
    assert store.jobs(p['id'])==[]
