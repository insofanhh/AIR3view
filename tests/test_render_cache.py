import copy
import json
import shutil
import numpy as np
import av
import pytest
from backend import store, render, render_cache
from backend.media import FFMPEG, run, probe, Cancelled
from backend.providers import voice_hash
from backend.render_progress import parse_progress, run_progress
from backend.timeline import build


@pytest.fixture
def media_project(tmp_path,monkeypatch):
    if not shutil.which(FFMPEG):pytest.skip('FFmpeg required')
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'test.sqlite3');store.init()
    p=store.create('Render cache',dict(kind='upload',file='source.mp4'));folder=store.project_dir(p['id'])
    run([FFMPEG,'-y','-f','lavfi','-i','testsrc2=size=160x90:rate=30','-f','lavfi','-i','sine=frequency=220:sample_rate=48000',
         '-t','10','-c:v','libx264','-c:a','aac',folder/'source.mp4'])
    run([FFMPEG,'-y','-f','lavfi','-i','sine=frequency=880:sample_rate=48000','-t','5',folder/'voice.wav'])
    p['metadata']=probe(folder/'source.mp4')
    p['settings'].update(output_mode=None,render_encoder='cpu',hook_enabled=True,hook_start=8,hook_end=10,
                          title='',subtitles=True,background_mode='solid',duck_volume=.2)
    n=dict(id='n1',start=1,text='Narration through chunk boundaries',enabled=True,audio='voice.wav',duration=5,
           cues=[dict(id='n1c',start=0,end=5,text='Narration through chunk boundaries')])
    n['audio_hash']=voice_hash(n,p['settings']);p['narrations']=[n]
    def chopped(project,strict=False):
        timeline=build(project,strict);clips=[]
        for clip in timeline['clips']:
            start=clip['start']
            while start<clip['end']-.001:
                end=min(clip['end'],start+2)
                clips.append({**clip,'start':start,'end':end,'source_start':clip['source_start']+start-clip['start'],
                              'source_end':clip['source_start']+end-clip['start']})
                start=end
        timeline['clips']=clips
        return timeline
    monkeypatch.setattr(render_cache,'build',chopped);monkeypatch.setattr(render_cache,'CHUNK_SECONDS',2)
    return p,folder


def decode_audio(path):
    with av.open(str(path)) as c:return np.concatenate([x.to_ndarray()[0] for x in c.decode(audio=0)])


def packet_hashes(path):
    import hashlib
    with av.open(str(path)) as c:
        return [hashlib.sha256(bytes(p)).hexdigest() for p in c.demux(video=0) if p.size]


def test_volume_only_change_reuses_all_video_packets_and_progress(media_project):
    p,folder=media_project;messages=[]
    render.render(p,lambda value,message:messages.append((value,message)),lambda:None,preview=True)
    first=p['preview_exports'][0];old_packets=packet_hashes(store.asset(p['id'],first['file']))
    assert first['cache']['video_rendered']==6
    assert any('Ghép MP4' in m for _,m in messages)
    assert [v for v,_ in messages]==sorted(v for v,_ in messages)
    p['settings']['voice_volume']=.5
    render.render(p,lambda *a:None,lambda:None,preview=True)
    second=p['preview_exports'][0]
    assert second['cache']['video_reused']==6 and second['cache']['video_rendered']==0
    assert not second['cache']['audio_reused']
    assert packet_hashes(store.asset(p['id'],second['file']))==old_packets
    old=decode_audio(store.asset(p['id'],first['file']));new=decode_audio(store.asset(p['id'],second['file']))
    def amp(samples):
        y=samples[round(4.2*48000):round(4.8*48000)]
        return abs(np.sum(y*np.exp(-2j*np.pi*880*np.arange(len(y))/48000)))/len(y)
    assert amp(new)/amp(old)==pytest.approx(.5,abs=.025)


def test_only_one_changed_subtitle_chunk_renders_again(media_project):
    p,_=media_project
    p['narrations'][0]['cues']=[dict(id='a',start=1.2,end=1.7,text='One caption')]
    render.render(p,lambda *a:None,lambda:None,preview=True)
    p['narrations'][0]['cues'][0]['text']='Changed caption'
    render.render(p,lambda *a:None,lambda:None,preview=True)
    cache=p['preview_exports'][0]['cache']
    assert cache['video_rendered']==1 and cache['video_reused']==5
    assert cache['audio_reused']


def test_rules_and_project_name_do_not_invalidate_identical_media(media_project):
    p,_=media_project
    render.render(p,lambda *a:None,lambda:None,preview=True)
    p['name']='New name';p['settings']['draft_rule']='Different instruction';p['settings']['provider']='gemini'
    render.render(p,lambda *a:None,lambda:None,preview=True)
    assert p['preview_exports'][0]['cache']==dict(video_reused=6,video_rendered=0,audio_reused=True,final_reused=True)


def test_cancel_keeps_completed_chunks_for_retry(media_project,monkeypatch):
    p,folder=media_project;original=render.render_part;calls=[]
    def interrupted(*args,**kwargs):
        calls.append(1)
        if len(calls)==3:raise Cancelled('stop')
        return original(*args,**kwargs)
    monkeypatch.setattr(render,'render_part',interrupted)
    with pytest.raises(Cancelled):render.render(p,lambda *a:None,lambda:None,preview=True)
    assert len(list((folder/'render-cache/video').glob('*/ready.json')))==2
    assert not p.get('preview_exports')
    monkeypatch.setattr(render,'render_part',original)
    render.render(p,lambda *a:None,lambda:None,preview=True)
    assert p['preview_exports'][0]['cache']['video_reused']==2


def test_chunked_frames_and_continuous_audio_match_single_pass_at_joins(media_project,monkeypatch):
    monkeypatch.setattr(render_cache,'render_workers',lambda *a:2)
    p,folder=media_project;timeline=render_cache.build(p,strict=True)
    direct=folder/'direct';direct.mkdir()
    old=render.render_part(p,timeline,timeline['parts'][0],direct,lambda:None,width=360)
    render.render(p,lambda *a:None,lambda:None,preview=True)
    out=p['preview_exports'][0]
    with av.open(str(store.asset(p['id'],old['file']))) as c:expected=[f.to_ndarray(format='rgb24') for f in c.decode(video=0)]
    with av.open(str(store.asset(p['id'],out['file']))) as c:actual=[f.to_ndarray(format='rgb24') for f in c.decode(video=0)]
    assert len(actual)==len(expected)==360
    for index in (0,59,60,61,119,120,121,179,180,239,240,299,300,359):
        assert np.abs(actual[index].astype(float)-expected[index].astype(float)).mean()<3
    wav=next((folder/'render-cache/audio').glob('*/mix.wav'))
    with av.open(str(wav)) as c:
        samples=np.concatenate([f.to_ndarray().reshape(-1,2)[:,0] for f in c.decode(audio=0)])
    assert len(samples)==12*48000
    decoded=decode_audio(store.asset(p['id'],out['file']))
    # A continuous AI tone spans the 4s and 6s video joins: no AAC seam/gap.
    for moment in (3.98,4.02,5.98,6.02):
        x=decoded[round(moment*48000):round((moment+.01)*48000)]
        assert np.sqrt(np.mean(x*x))>.04


def test_preview_is_limited_to_fifteen_seconds_at_cursor():
    timeline=dict(duration=100,parts=[dict(index=1,start=0,end=50),dict(index=2,start=50,end=100)])
    assert render_cache.preview_part(timeline,12)==dict(index=1,start=12,end=27,duration=15)
    assert render_cache.preview_part(timeline,49)==dict(index=1,start=49,end=50,duration=1)
    assert render_cache.preview_part(timeline,51)['index']==2


def test_scene_boundaries_are_preserved_instead_of_forcing_decode_resets():
    t=dict(clips=[dict(end=x) for x in (5,24,43,62,85,100)])
    rows=render_cache.chunks(t,dict(index=1,start=0,end=100,duration=100))
    assert all(c['end'] in (5,24,43,62,85,100) for c in rows)
    assert sum(c['duration'] for c in rows)==100


def test_progress_parser_handles_partial_and_na_values():
    assert parse_progress('out_time_us=2500000\nspeed=2.5x\nprogress=continue\n')==(2.5,2.5)
    assert parse_progress('out_time_us=N/A\nspeed=nan\n')==(0,0)


@pytest.mark.parametrize('free,expected',[(200,1),(900,2)])
def test_parallel_sessions_require_gpu_headroom(monkeypatch,free,expected):
    from types import SimpleNamespace
    monkeypatch.setattr(render_cache.os,'cpu_count',lambda:16)
    monkeypatch.setattr(render_cache.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=str(free)))
    assert render_cache.render_workers(1080,'nvenc',6)==expected
    assert render_cache.render_workers(360,'nvenc',6)==1
    assert render_cache.render_workers(1080,'cpu',6)==1


def test_corrupt_chunk_is_rebuilt_without_discarding_siblings(media_project):
    p,folder=media_project
    render.render(p,lambda *a:None,lambda:None,preview=True)
    chunk=next((folder/'render-cache/video').glob('*/part-001.mp4'))
    chunk.write_bytes(b'truncated')
    render.render(p,lambda *a:None,lambda:None,preview=True)
    assert p['preview_exports'][0]['cache']['video_rendered']==1
    assert p['preview_exports'][0]['cache']['video_reused']==5


def test_preview_filters_work_at_preview_resolution(media_project):
    p,folder=media_project
    render.render(p,lambda *a:None,lambda:None,preview=True,preview_start=3)
    graphs=list((folder/'render-cache/video').glob('*/part-001.filters.txt'))
    assert graphs
    for path in graphs:
        text=path.read_text('utf-8')
        assert 'scale=1080:' not in text and 's=1080x1920' not in text
    assert p['preview_exports'][0]['preview_start']==3


def test_gpu_failure_reduces_workers_then_rebuilds_with_one_consistent_cpu_encoder(media_project,monkeypatch):
    p,_=media_project;p['settings']['render_encoder']='auto'
    monkeypatch.setattr(render,'nvenc_available',lambda:True)
    monkeypatch.setattr(render_cache,'render_workers',lambda *a:2)
    original=render.render_part;messages=[]
    def fail_gpu(project,*a,**kw):
        if project['settings']['render_encoder']=='nvenc':raise RuntimeError('OpenEncodeSessionEx failed: out of memory')
        return original(project,*a,**kw)
    monkeypatch.setattr(render,'render_part',fail_gpu)
    render.render(p,lambda value,message:messages.append(message),lambda:None,preview=True)
    assert p['preview_exports'][0]['encoder']=='cpu'
    assert p['preview_exports'][0]['cache']['video_rendered']==6
    assert any('giảm còn một luồng' in m for m in messages)
    assert any('chuyển cả phần sang CPU' in m for m in messages)
